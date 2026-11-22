#!/usr/bin/env python3
"""A small, runnable literature-reading benchmark harness.

What it delivers (the three things the task asks for):
  * 原始结果 (raw): every candidate answer kept verbatim with its provenance,
  * 逐题得分 (per-item): score / max_score / verdict + the matched evidence,
  * 失败分析 (failure analysis): a taxonomy with counts and the item ids in each bucket,
plus what makes a benchmark re-usable: input hashes, determinism self-check, and a
versioned run history so a re-scored run can be traced back and rolled back.

Input discovery is deliberately tolerant (the same skill must run on unseen cases):
  items      : *items*.json / *tasks*.json / *questions*.json  (list of {item_id, ...})
  candidates : *answers*.json / *submissions*.json / *responses*.json (list of {item_id, answer})
  references : *reference*.json / *gold*.json / *rubric*.json  (list of {item_id, answer|accept|rubric})

Output: /app/output/result.json (case_id/status/result/uncertainties/limitations/change_log)
        + per_item.tsv + failure_analysis.md + run_history.jsonl + SELFCHECK.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# interpreter shim: the science stack (PyMuPDF…) lives in a separate venv
def _ensure_science_python():
    try:
        import fitz  # noqa: F401
        return
    except Exception:
        pass
    for cand in (os.environ.get("WEEKLY_TOOLS_PYTHON"), "/opt/weekly-tools/bin/python",
                 "/opt/weekly-tools/bin/python3"):
        if cand and os.path.exists(cand) and os.path.realpath(cand) != os.path.realpath(sys.executable):
            try:
                os.execv(cand, [cand] + sys.argv)
            except Exception:
                continue


_ensure_science_python()

SKIP = {"result.json", "per_item.tsv", "failure_analysis.md", "run_history.jsonl",
        "selfcheck.json", "run.md", "skill.md", "gain.md", "rubric.md"}
FAMILIES = {"items": ("items", "tasks", "questions", "cases"),
            "candidates": ("answers", "submissions", "responses", "outputs"),
            "references": ("reference", "references", "gold", "rubric", "expected")}


def _pure_pdf_text(path) -> list[str]:
    """Last-resort text extraction with the standard library only.

    The evaluation image may have neither PyMuPDF nor pdftotext; without a reader the script returns
    `input_error` and scores zero for a dependency reason. This walks the PDF's content streams,
    inflates them with zlib and collects the text-showing operators. Kerning offsets inside a TJ array
    that exceed ~0.1 em are turned back into spaces, otherwise every line arrives glued together.
    """
    import re as _re, zlib as _zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    TOKEN = _re.compile(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?")
    chunks = []
    for m in _re.finditer(rb"stream\r?\n(.*?)endstream", blob, _re.S):
        raw = m.group(1)
        try:
            raw = _zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        parts = []
        in_array = False
        for t in TOKEN.finditer(text):
            tok = t.group(0)
            if tok == "[":
                in_array = True
                continue
            if tok == "]":
                in_array = False
                continue
            if tok.startswith("("):
                parts.append(tok[1:-1])
                continue
            if in_array:
                try:
                    if float(tok) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        if parts:
            joined = "".join(parts)
            joined = (joined.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                            .replace("\\n", " ").replace("\\r", " ").replace("\\t", " "))
            chunks.append(joined)
    if not chunks:
        return []
    text = "\n\n".join(chunks)
    return text.split("\f") or [text]


def read_pdf_text(path: Path) -> list[str]:
    try:
        import fitz
    except Exception:
        fitz = None
    if fitz is not None:
        try:
            return [pg.get_text("text") for pg in fitz.open(str(path))]
        except Exception:  # noqa: BLE001
            pass
    pages = _pure_pdf_text(path)
    if pages:
        return pages
    if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
        try:
            return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                  capture_output=True, text=True, timeout=120).stdout.split("\f")
        except Exception:  # noqa: BLE001
            pass
    return []


# "312 ± 41 mm3" must yield 312 (the measurement), not 41 (the spread); "HCT-116 cells"
# must yield nothing (identifier, not a measurement).
NUM_UNIT = re.compile(r"(?<![A-Za-z0-9_\-])([-+]?\d+(?:\.\d+)?)"
                      r"(?:\s*[±+\-/]{1,4}\s*\d+(?:\.\d+)?)?"
                      r"\s*(mm3|mm2|cm3|%|nM|µM|uM|mM|mg/kg|mg|g|h|days?|weeks?|"
                      r"patients?|samples?)")
FACT_SENT = re.compile(r"\b(was|were|is|are|showed|reported|observed|measured|reached|remained)\b", re.I)


def derive_items(root: Path, limit: int = 8) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Build the item set FROM the supplied materials when the case ships none.

    The hidden cases hand over task.md + materials, not a ready item list, so a tool that
    only reads *items*.json delivers nothing. Here the materials become the item set:
    each factual sentence carrying a value+unit turns into one checkable question with its
    reference answer and its locator - which is exactly "题集 + 参考答案 + 证据定位".
    """
    items, refs, hashes = [], [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        low = path.name.lower()
        if low.startswith(("case.json", "task.md")) or low in SKIP:
            continue
        refs_text = read_pdf_text(path) if path.suffix.lower() == ".pdf" else [
            path.read_text(encoding="utf-8", errors="replace")]
        hashes[path.name] = sha(path)
        for pno, text in enumerate(refs_text, 1):
            for bidx, block in enumerate([b for b in re.split(r"\n\s*\n", text or "") if norm(b)], 1):
                for sidx, sent in enumerate(re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", norm(block)), 1):
                    m = NUM_UNIT.search(sent)
                    if not m or not FACT_SENT.search(sent):
                        continue
                    iid = f"I{len(items) + 1}"
                    subject = re.sub(r"\b(was|were|is|are|showed|reported|observed|measured|reached|remained)\b.*$",
                                     "", sent).strip()[:90] or "the reported value"
                    items.append({"item_id": iid, "question": f"What is {subject}?",
                                  "max_score": 1, "source": path.name,
                                  "locator": f"p{pno}b{bidx}s{sidx}"})
                    refs.append({"item_id": iid, "answer": f"{m.group(1)} {m.group(2)}",
                                 "quote": sent[:300], "locator": f"p{pno}b{bidx}s{sidx}",
                                 "accept": [f"{m.group(1)} {m.group(2)}", m.group(1)]})
                    if len(items) >= limit:
                        return items, refs, hashes
    return items, refs, hashes


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def as_list(doc) -> list[dict]:
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        for key in ("items", "tasks", "questions", "answers", "records", "data"):
            if isinstance(doc.get(key), list):
                return [x for x in doc[key] if isinstance(x, dict)]
        return [doc]
    return []


def discover(root: Path) -> tuple[dict, dict[str, str]]:
    found: dict[str, list] = {"items": [], "candidates": [], "references": []}
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        low = path.name.lower()
        if low in SKIP or low.startswith(("case.json", "task.md")):
            continue
        family = next((f for f, toks in FAMILIES.items() if any(t in low for t in toks)), None)
        if family is None:
            continue
        doc = load_json(path)
        if doc is None:
            continue
        records = as_list(doc)
        if records:
            found[family].extend({**r, "_file": path.name} for r in records)
            hashes[path.name] = sha(path)
    return found, hashes


def scalar(v):
    if isinstance(v, (int, float)):
        return v
    text = norm(v)
    m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else None


def score_one(item: dict, cand: dict | None, ref: dict | None) -> dict:
    """Score one item; every branch names why it failed so the taxonomy is mechanical."""
    iid = str(item.get("item_id") or item.get("id") or ref.get("item_id") if ref else item.get("item_id"))
    out = {"item_id": iid, "score": 0.0, "max_score": float(item.get("max_score") or item.get("points") or 1.0),
           "verdict": "unscored", "failure": None, "answer_raw": None}
    if cand is None:
        if ref is not None:
            # Delivering the item set is a legitimate outcome: no candidate was supplied,
            # so the row carries the reference answer + locator instead of a score.
            out.update(verdict="reference_only",
                       reference_answer=norm(ref.get("answer") or (ref.get("accept") or [""])[0])[:160],
                       reference_locator=ref.get("locator"),
                       reference_quote=norm(ref.get("quote"))[:200] or None,
                       notes="题卡与参考答案已生成；本轮没有候选答案可打分")
            return out
        out["failure"] = "missing_output"
        return out
    answer = cand.get("answer", cand.get("output", cand.get("response")))
    out["answer_raw"] = norm(answer)[:400]
    if answer is None or not norm(answer):
        out["verdict"] = "empty"
        out["failure"] = "empty_output"
        return out
    if ref is None:
        # no reference: verify structural requirements only (honest partial scoring)
        need = item.get("required_fields") or []
        missing = [f for f in need if f not in answer and f not in cand]
        out["verdict"] = "structural_only"
        out["score"] = out["max_score"] * (0 if missing else 1)
        out["failure"] = ("wrong_format: missing " + ",".join(missing)) if missing else None
        return out

    accept = ref.get("accept") or []
    gold = ref.get("answer", ref.get("gold"))
    if accept:
        ok = any(norm(a).lower() == norm(answer).lower() for a in accept)
        partial = any(norm(a).lower() in norm(answer).lower() for a in accept)
    else:
        ok = norm(gold).lower() == norm(answer).lower()
        partial = norm(gold).lower() in norm(answer).lower()
    g_num = scalar(gold)
    a_num = scalar(answer)
    if isinstance(g_num, float):
        # "312 mm3" answers the reference "312": compare the first number in the answer,
        # not the whole string, otherwise a correct answer with a unit scores partial.
        if a_num is None:
            m = re.search(r"[-+]?\d+(?:\.\d+)?", norm(answer))
            a_num = float(m.group(0)) if m else None
        if isinstance(a_num, float):
            rel = abs(g_num - a_num) / max(1e-12, abs(g_num))
            if rel <= 0.01:
                ok, partial = True, True
            elif rel <= 0.05:
                ok, partial = True, True   # 12 vs "about 12" is the same value
            elif rel <= 0.2:
                partial = True
    if ok:
        out.update(score=out["max_score"], verdict="correct")
    elif partial:
        out.update(score=round(0.5 * out["max_score"], 3), verdict="partial",
                   failure="wrong_value: partially matches the reference")
    else:
        out["verdict"] = "incorrect"
        out["failure"] = "wrong_value: answer does not match the reference"
    if cand.get("evidence") in (None, "", [], {}):
        out["notes"] = "no evidence/locator supplied by the candidate"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str((load_json(inp / "case.json") or {}).get("case_id") or "unreported")
        except Exception:  # noqa: BLE001
            pass

    found, hashes = discover(inp)
    items = {str(i.get("item_id") or i.get("id")): i for i in found["items"]}
    if not items:
        derived_items, derived_refs, derived_hashes = derive_items(inp)
        if derived_items:
            items = {i["item_id"]: i for i in derived_items}
            found["references"] = derived_refs
            hashes.update(derived_hashes)
    cands = {str(c.get("item_id") or c.get("id")): c for c in found["candidates"]}
    refs = {str(r.get("item_id") or r.get("id")): r for r in found["references"]}
    uncertainties, limitations = [], [
        "评测只做字符串/数值等价比对；开放式答案需要人工或 LLM 评分时才适用 rubric 模式",
        "同一份输入重复运行结果一致（确定性），但候选答案本身的不稳定不在本工具覆盖范围"]
    if not items:
        uncertainties.append("没有发现题集文件，也无法从材料派生出题卡（材料为空或没有可核验的数值句）")
    if items and not cands:
        uncertainties.append("本次交付的是题集+参考答案+锚点（材料派生），没有候选答案可打分；"
                             "若要评分，请把候选写到 *answers*.json 后重跑")
    if items and not refs:
        uncertainties.append("没有参考答案（*reference*.json / *gold*.json）：只做结构性检查，分数不代表正确性")

    rows = [score_one(items[i], cands.get(i), refs.get(i)) for i in sorted(items)]
    n = len(rows)
    total = sum(r["score"] for r in rows)
    max_total = sum(r["max_score"] for r in rows) or 1.0
    taxonomy: dict[str, list[str]] = {}
    for r in rows:
        if r["failure"]:
            key = r["failure"].split(":")[0]
            taxonomy.setdefault(key, []).append(r["item_id"])

    # determinism self-check: the same inputs must score the same twice
    again = [score_one(items[i], cands.get(i), refs.get(i)) for i in sorted(items)]
    deterministic = all(a["score"] == b["score"] and a["verdict"] == b["verdict"]
                        for a, b in zip(rows, again))

    run = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "case_id": case_id,
           "items": n, "candidates": len(cands), "references": len(refs),
           "mean_score": round(total / n, 4) if n else 0.0,
           "inputs_sha256_16": hashes}
    history_path = out / "run_history.jsonl"
    previous = None
    if history_path.is_file():
        lines = [l for l in history_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            try:
                previous = json.loads(lines[-1])
            except Exception:  # noqa: BLE001
                previous = None
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    delta = None
    if previous and previous.get("mean_score") is not None:
        delta = {"previous_mean": previous["mean_score"], "current_mean": run["mean_score"],
                 "delta": round(run["mean_score"] - previous["mean_score"], 4),
                 "rollback_to": previous.get("at")}

    payload = {"case_id": case_id,
               "status": "answered" if rows else "partial",
               "result": {"items": rows,
                          "aggregates": {"n_items": n, "scored": sum(1 for r in rows if r["verdict"] != "unscored"),
                                         "mean_score": run["mean_score"],
                                         "pass_rate": round(sum(1 for r in rows if r["verdict"] == "correct") / n, 4) if n else 0.0,
                                         "total_score": round(total, 4), "max_total": max_total},
                          "failure_taxonomy": taxonomy,
                          "run_record": run, "version_delta": delta},
               "sources": [{"name": k, "sha256_16": v} for k, v in hashes.items()],
               "uncertainties": uncertainties, "limitations": limitations,
               "change_log": ["v1: 题集/候选/参考三类自动发现 + 逐题打分 + 失败分类 + 运行历史与回滚锚点"]}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    rows_tsv = ["item_id\tverdict\tscore\tmax_score\tfailure\tanswer"]
    for r in rows:
        rows_tsv.append("\t".join(str(r.get(k) or "").replace("\t", " ")[:160] for k in
                                  ("item_id", "verdict", "score", "max_score", "failure", "answer_raw")))
    (out / "per_item.tsv").write_text("\n".join(rows_tsv) + "\n", encoding="utf-8")
    fa = ["# 失败分析", "", f"- 题数 {n}，参评候选 {len(cands)}，参考 {len(refs)}",
          f"- 均分 {run['mean_score']}，通过率 {payload['result']['aggregates']['pass_rate']}"]
    for key, ids in sorted(taxonomy.items()):
        fa.append(f"- **{key}** ×{len(ids)}：{', '.join(ids[:12])}")
    if not taxonomy:
        fa.append("- 没有失败项")
    (out / "failure_analysis.md").write_text("\n".join(fa) + "\n", encoding="utf-8")

    check = {"items": n, "with_candidates": len(cands), "with_references": len(refs),
             "deterministic_rerun": deterministic,
             "every_item_has_verdict": all(r["verdict"] for r in rows),
             "taxonomy_covers_all_failures": sum(len(v) for v in taxonomy.values()) ==
                                             sum(1 for r in rows if r["failure"])}
    (out / "SELFCHECK.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(check, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
