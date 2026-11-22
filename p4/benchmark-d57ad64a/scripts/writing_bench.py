#!/usr/bin/env python3
"""Writing-benchmark evaluation: rubric anchors, acceptable variants, rater agreement.

Why this shape: the writing task is not "score one essay" but "make the item set
judgeable". So the tool scores candidates against explicit rubric anchors, separates
acceptable stylistic variation from real errors, and measures how well two independent
raters agree (percent agreement + Cohen's kappa) so an anchor can be fixed instead of
silently re-scoring.

Inputs (discovered by filename family, never hard-coded):
  items     : *items*.json / *tasks*.json           [{item_id, prompt, criteria:[{name,weight,anchors?}], required_sections?}]
  candidates: *answers*.json / *submissions*.json   [{item_id, answer}]
  references: *reference*.json / *gold*.json        [{item_id, accept:[...], reject:[...]}]
  raters    : *rater*.json / *annot*.json           [{rater, item_id, scores:{criterion: 0..1}}]

Output /app/output: result.json (per-item scores with matched evidence, aggregates,
failure taxonomy, rater agreement, run record), per_item.tsv, report.md, SELFCHECK.json.
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

SKIP = {"result.json", "per_item.tsv", "report.md", "selfcheck.json", "run.md",
        "skill.md", "gain.md", "rubric.md"}
FAMILIES = {"items": ("items", "tasks", "questions"),
            "candidates": ("answers", "submissions", "drafts"),
            "references": ("reference", "gold", "expected"),
            "raters": ("rater", "annot", "judge")}


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


def read_text_any(path: Path) -> list[str]:
    if path.suffix.lower() != ".pdf":
        return [path.read_text(encoding="utf-8", errors="replace")]
    _pages = _pure_pdf_text(path)
    if _pages:
        return _pages
    try:
        import fitz
    except Exception:
        fitz = None
    if fitz is not None:
        try:
            return [pg.get_text("text") for pg in fitz.open(str(path))]
        except Exception:  # noqa: BLE001
            pass
    if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
        try:
            return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                  capture_output=True, text=True, timeout=120).stdout.split("\f")
        except Exception:  # noqa: BLE001
            pass
    return []


NUM_UNIT = re.compile(r"(?<![A-Za-z0-9_\-])([-+]?\d+(?:\.\d+)?)"
                      r"(?:\s*[±+\-/]{1,4}\s*\d+(?:\.\d+)?)?\s*(mm3|%|nM|µM|mg/kg|mg|days?|patients?)")
FACT = re.compile(r"\b(was|were|is|are|showed|reported|observed|measured)\b", re.I)
HEAD = re.compile(r"^\s*(#{1,3}\s*)?([A-Z][A-Za-z /-]{2,40})\s*$")


def derive_items(root: Path, limit: int = 6) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Build writing-rubric items from the supplied materials when the case ships none.

    Each material section becomes one item whose criteria are the checkable parts of a
    writing deliverable (facts present, numbers with units, section coverage); the
    reference answer is the section's own factual sentences, so the item set is derivable
    from real material instead of being invented.
    """
    items, refs, hashes = [], [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        low = path.name.lower()
        if low.startswith("case.json") or low in SKIP:
            continue
        pages = read_text_any(path)
        hashes[path.name] = sha(path)
        section = ""
        for pno, text in enumerate(pages, 1):
            in_refs = False
            for line in (text or "").splitlines():
                if re.match(r"^\s*(references|bibliography|参考文献)\s*$", line, re.I):
                    in_refs = True
                if in_refs:
                    continue
                m = HEAD.match(line)
                if m and len(m.group(2).split()) <= 5:
                    section = m.group(2).strip()
                    continue
                sent = norm(line)
                if len(sent) < 40:
                    continue
                nums = NUM_UNIT.findall(sent)
                if not nums and not FACT.search(sent):
                    continue
                iid = f"W{len(items) + 1}"
                items.append({"item_id": iid,
                              "prompt": f"Rewrite the '{section or 'results'}' content with every stated fact and value preserved",
                              "required_sections": [section] if section else [],
                              "criteria": [{"name": "accuracy", "weight": 2},
                                           {"name": "completeness", "weight": 1},
                                           {"name": "structure", "weight": 1}]})
                refs.append({"item_id": iid, "accept": [sent[:200]],
                             "reject": [], "quote": sent[:300], "locator": f"p{pno}"})
                if len(items) >= limit:
                    return items, refs, hashes
    return items, refs, hashes


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def as_list(doc) -> list[dict]:
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        for key in ("items", "tasks", "answers", "records", "annotations", "data"):
            if isinstance(doc.get(key), list):
                return [x for x in doc[key] if isinstance(x, dict)]
        return [doc]
    return []


def discover(root: Path) -> tuple[dict, dict[str, str]]:
    found: dict[str, list] = {k: [] for k in FAMILIES}
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        low = path.name.lower()
        if low in SKIP or low.startswith("case.json"):
            continue
        fam = next((f for f, toks in FAMILIES.items() if any(t in low for t in toks)), None)
        if not fam:
            continue
        doc = load(path)
        rows = as_list(doc) if doc is not None else []
        if rows:
            found[fam].extend({**r, "_file": path.name} for r in rows)
            hashes[path.name] = sha(path)
    return found, hashes


def sections_present(answer: str, wanted: list[str]) -> list[str]:
    low = answer.lower()
    return [w for w in wanted if w.lower() not in low]


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    """Cohen's kappa for two raters on categorical scores."""
    if len(pairs) < 2:
        return None
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    if len(cats) < 2:
        return 1.0 if len({a for a, _ in pairs}) == 1 else 0.0
    total = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / total
    pa = {c: sum(1 for a, _ in pairs if a == c) / total for c in cats}
    pb = {c: sum(1 for _, b in pairs if b == c) / total for c in cats}
    expected = sum(pa[c] * pb[c] for c in cats)
    if expected >= 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        case_id = str((load(inp / "case.json") or {}).get("case_id") or "unreported")

    found, hashes = discover(inp)
    items = {str(i.get("item_id") or i.get("id")): i for i in found["items"]}
    if not items:
        d_items, d_refs, d_hashes = derive_items(inp)
        if d_items:
            items = {i["item_id"]: i for i in d_items}
            found["references"] = d_refs
            hashes.update(d_hashes)
    cands = {str(c.get("item_id") or c.get("id")): c for c in found["candidates"]}
    refs = {str(r.get("item_id") or r.get("id")): r for r in found["references"]}
    raters: dict[str, dict[str, dict]] = {}
    for r in found["raters"]:
        who = str(r.get("rater") or r.get("annotator") or r.get("_file"))
        raters.setdefault(who, {})[str(r.get("item_id") or r.get("id"))] = r.get("scores") or {}

    uncertainties, limitations = [], [
        "评分按 rubric 权重做加权求和；锚点判据是字符串/结构匹配，语义级判断仍需人工或 LLM",
        "Cohen's kappa 只在两名评分员、同一批题上计算；类别过少时 kappa 会退化（小样本已知问题）"]
    if not items:
        uncertainties.append("未发现题集文件（*items*.json / *tasks*.json）")
    if items and not cands:
        uncertainties.append("本次交付的是题集+参考答案+锚点（材料派生）；要评分请把候选写到 *answers*.json 后重跑")
    if not raters:
        uncertainties.append("没有评分员记录（*rater*.json / *annot*.json），无法计算标注一致性")

    rows = []
    for iid, item in sorted(items.items()):
        crit = item.get("criteria") or [{"name": "overall", "weight": 1.0}]
        wsum = sum(float(c.get("weight") or 1.0) for c in crit) or 1.0
        cand = cands.get(iid)
        answer = norm((cand or {}).get("answer") or (cand or {}).get("draft") or "")
        ref = refs.get(iid) or {}
        row = {"item_id": iid, "max_score": round(wsum, 3), "score": 0.0,
               "criteria": [], "failure": None, "answer_chars": len(answer)}
        if not answer:
            if cand is None and ref:
                _acc = ref.get("accept") or []
                row.update(verdict="reference_only",
                           reference_answer=norm(_acc[0] if _acc else "")[:160],
                           reference_locator=ref.get("locator"),
                           notes="题卡与参考答案已生成；本轮没有候选可打分")
                rows.append(row)
                continue
            row["failure"] = "missing_output" if cand is None else "empty_output"
            row["verdict"] = "empty"
            rows.append(row)
            continue
        missing = sections_present(answer, item.get("required_sections") or [])
        accept = [norm(a) for a in (ref.get("accept") or [])]
        reject = [norm(a) for a in (ref.get("reject") or [])]
        accepted = any(a.lower() in answer.lower() for a in accept) if accept else None
        rejected = [r for r in reject if r.lower() in answer.lower()]
        earned = 0.0
        for c in crit:
            name = str(c.get("name") or "overall")
            w = float(c.get("weight") or 1.0)
            got = 1.0
            why = "anchor satisfied"
            if missing and name.lower() in ("structure", "completeness", "format"):
                got, why = 0.0, "missing section(s): " + ",".join(missing)
            if rejected and name.lower() in ("accuracy", "fact", "citation"):
                got, why = 0.0, "contains a rejected variant: " + rejected[0][:60]
            if accepted is False and name.lower() in ("accuracy", "fact"):
                got, why = 0.5, "not an accepted variant of the reference"
            earned += got * w
            row["criteria"].append({"criterion": name, "weight": w, "score": got, "why": why})
        row["score"] = round(earned, 3)
        if missing:
            row["failure"] = "wrong_format: missing " + ",".join(missing)
        elif rejected:
            row["failure"] = "wrong_fact: rejected variant present"
        elif accepted is False:
            row["failure"] = "style_or_variant: differs from every accepted variant"
        row["verdict"] = "correct" if not row["failure"] else "partial"
        rows.append(row)

    # ---- rater agreement (per criterion, over items both raters scored)
    agreement = {}
    if len(raters) >= 2:
        names = sorted(raters)[:2]
        a, b = raters[names[0]], raters[names[1]]
        common = sorted(set(a) & set(b))
        per_crit: dict[str, list[tuple[int, int]]] = {}
        for iid in common:
            for crit, val in (a[iid] or {}).items():
                if crit in (b[iid] or {}):
                    per_crit.setdefault(crit, []).append((int(round(float(val))), int(round(float(b[iid][crit])))))
        for crit, pairs in per_crit.items():
            agree = sum(1 for x, y in pairs if x == y) / len(pairs)
            agreement[crit] = {"n": len(pairs), "percent_agreement": round(agree, 4),
                               "cohen_kappa": kappa(pairs)}

    n = len(rows)
    total = sum(r["score"] for r in rows)
    max_total = sum(r["max_score"] for r in rows) or 1.0
    taxonomy: dict[str, list[str]] = {}
    for r in rows:
        if r["failure"]:
            taxonomy.setdefault(r["failure"].split(":")[0], []).append(r["item_id"])
    run = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "case_id": case_id, "items": n,
           "candidates": len(cands), "references": len(refs), "raters": sorted(raters),
           "mean_score": round(total / n, 4) if n else 0.0, "inputs_sha256_16": hashes}

    payload = {"case_id": case_id, "status": "answered" if rows else "partial",
               "result": {"items": rows,
                          "aggregates": {"n_items": n, "mean_score": run["mean_score"],
                                         "total_score": round(total, 3), "max_total": round(max_total, 3),
                                         "pass_rate": round(sum(1 for r in rows if r["verdict"] == "correct") / n, 4) if n else 0.0},
                          "failure_taxonomy": taxonomy, "rater_agreement": agreement, "run_record": run},
               "sources": [{"name": k, "sha256_16": v} for k, v in hashes.items()],
               "uncertainties": uncertainties, "limitations": limitations,
               "change_log": ["v1: rubric 加权评分 + 可接受变体/拒绝变体 + 双评一致性与 kappa + 失败分类"]}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    tsv = ["item_id\tverdict\tscore\tmax_score\tfailure\tchars"]
    for r in rows:
        tsv.append("\t".join(str(r.get(k) or "").replace("\t", " ")[:160] for k in
                             ("item_id", "verdict", "score", "max_score", "failure", "answer_chars")))
    (out / "per_item.tsv").write_text("\n".join(tsv) + "\n", encoding="utf-8")

    rep = ["# 评测报告", "", f"- 题数 {n}｜均分 {run['mean_score']}｜通过率 {payload['result']['aggregates']['pass_rate']}"]
    for r in rows:
        rep.append(f"## {r['item_id']} — {r['verdict']}（{r['score']}/{r['max_score']}）")
        if r["failure"]:
            rep.append(f"- 失败类型：{r['failure']}")
        for c in r["criteria"]:
            rep.append(f"- {c['criterion']}：{c['score']}×w{c['weight']} — {c['why']}")
        if r["failure"]:
            rep.append(f"- 改进建议：{'补上缺失小节' if 'missing' in (r['failure'] or '') else '对照参考变体重写该处，保留事实与引用不动'}")
    if agreement:
        rep += ["", "## 标注一致性"]
        for crit, a in agreement.items():
            rep.append(f"- {crit}: n={a['n']}, 一致率 {a['percent_agreement']}, kappa={a['cohen_kappa']}")
    (out / "report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

    check = {"items": n, "with_candidates": len(cands), "raters": len(raters),
             "every_item_has_verdict": all(r["verdict"] for r in rows),
             "taxonomy_covers_all_failures": sum(len(v) for v in taxonomy.values()) ==
                                             sum(1 for r in rows if r["failure"]),
             "agreement_computed": bool(agreement)}
    (out / "SELFCHECK.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(check, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
