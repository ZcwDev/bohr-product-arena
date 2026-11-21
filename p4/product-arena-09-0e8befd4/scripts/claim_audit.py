#!/usr/bin/env python3
"""claim_audit — audit every claim of a report against a supplied evidence corpus, verbatim.

The contract of this task is explicit and unforgiving: `audit.json` must carry exactly one entry per claim_id
with `status` in {supported, contradicted, insufficient}, `evidence` as `doc_id#section_id` pairs that exist in
the corpus, and a short `audit_note`; `annotated_report.md` must repeat the same statuses in the same order.
Any quotation that cannot be matched verbatim scores that claim zero, so this engine never paraphrases evidence:
it cites whole sentences that it copied out of the corpus, and it says `insufficient` rather than guessing.

How a status is decided (deterministic, auditable):
  * the claim is decomposed into its content units (numbers with units, latin terms, CJK bigrams);
  * a corpus section is a *candidate* when it shares enough of those units;
  * `supported`   — a candidate repeats the claim's key number/subject without a negator or a larger/newer value;
  * `contradicted`— a candidate states the opposite (negator near the subject) or a materially different value,
                    or the only support lives in a section that has been superseded;
  * `insufficient`— no candidate clears the threshold, or the corpus only covers part of the claim;
  * superseded sections never make a claim `supported`; they can only contradict.

Outputs:
  audit.json            claims[] · status · evidence[] · audit_note
  annotated_report.md   `ID | status | [doc_id#section_id], ...` (or 无有效证据), one line per claim, same order
  check_report.json     self-checks: ids complete & unique, status enum, evidence resolvable, quotes verbatim
  result.json           counts · gaps · limitations · verification · metadata

usage:
  claim_audit.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _meta import attach as meta_attach
except Exception:
    def meta_attach(inp, out):
        return {"unavailable": True}

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from zz_author import dump_blocks, run_pair as author_run, flat as _flat_q
except Exception:                                     # 缺件时退化为"只用脚本推导"，不能因此崩掉
    def dump_blocks(blocks, out, name="blocks.tsv"):
        return 0

    def author_run(path, blocks, out, spec):
        return [], {"checks": {"all_passed": False}, "problems": ["zz_author 缺失"]}, False

    def _flat_q(t):
        return re.sub(r"\s+", "", str(t or ""))

NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|％|°C|℃|µm|μm|um|nm|mm|cm|m|MPa|GPa|kPa|N|kg|mg|µg|ug|g|"
                 r"mL|µL|uL|L|h|min|s|days?|weeks?|Hz|kHz|J/mm³|J/cm³|W/mK|次|件|倍|个|例)")
# bare 未/无/不 fire inside ordinary compounds (未熔合 unmelted, 无损, 不足), so only explicit negation counts
NEG = re.compile(r"\b(not|no|none|never|failed|absence|lack of|does not|do not)\b|"
                 r"并没有|没有发现|并非|并未|不支持|相反|不存在|未发现|未见|无法|不能|已被推翻|相反地", re.I)
STOP = set("""的 了 是 在 和 与 对 有 无 也 都 而 及 或 等 一个 进行 通过 可以 使用 表明 提出 不同 具有 以及 这些
这种 目前 相关 影响 作用 由于 因此 但是 如果 需要 我们 该 本文 该研究 the a an of in on for with from that
this these those is are was were be been and or to by as at it its""".split())


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def units(text: str) -> set[str]:
    """Content units: numbers with units, latin words, CJK bigrams — the same space for claim and corpus."""
    out = set()
    for m in NUM.finditer(text):
        out.add(m.group(0).replace(" ", "").replace("％", "%"))
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
        lw = w.lower()
        if lw not in STOP:
            out.add(lw)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 4:
            if run not in STOP:
                out.add(run)
        else:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in STOP:
                    out.add(bg)
    return out


def load_corpus(inp: Path) -> list[dict]:
    """corpus.json: documents with doc_id/status/superseded_by and sections with section_id/text."""
    path = None
    for cand in ("corpus.json", "evidence.json"):
        if (inp / cand).is_file():
            path = inp / cand
            break
    if path is None:
        for p in inp.rglob("corpus*.json"):
            path = p
            break
    docs: list[dict] = []
    if path is None:
        return docs
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("documents") or data.get("docs") or []
    for d in items:
        did = str(d.get("doc_id") or d.get("id") or "")
        superseded = bool(d.get("superseded_by")) or str(d.get("status", "")).lower() in ("superseded",
                                                                                        "retracted")
        for s in d.get("sections") or d.get("paragraphs") or []:
            sid = str(s.get("section_id") or s.get("id") or "")
            text = norm(s.get("text") or s.get("content") or "")
            if did and sid and text:
                docs.append({"doc_id": did, "section_id": sid, "text": text,
                             "superseded": superseded,
                             "superseded_by": d.get("superseded_by")})
    return docs


def load_claims(inp: Path) -> list[dict]:
    """claims.json: [{claim_id, text/claim, citation/doc_id+section_id}] — the original citation is checked too."""
    path = None
    for cand in ("claims.json", "assertions.json"):
        if (inp / cand).is_file():
            path = inp / cand
            break
    if path is None:
        for p in inp.rglob("claims*.json"):
            path = p
            break
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("claims") or []
    out = []
    for i, c in enumerate(items, 1):
        if isinstance(c, str):
            out.append({"claim_id": f"C{i}", "text": c, "orig": []})
            continue
        cid = str(c.get("claim_id") or c.get("id") or f"C{i}")
        text = norm(c.get("text") or c.get("claim") or "")
        orig = c.get("citation") or c.get("citations") or c.get("refs") or c.get("evidence") or []
        norm_orig = []
        for o in (orig if isinstance(orig, list) else [orig]):
            if isinstance(o, dict):
                norm_orig.append({"doc_id": str(o.get("doc_id") or o.get("doc") or ""),
                                  "section_id": str(o.get("section_id") or o.get("section") or "")})
            elif isinstance(o, str) and "#" in o:
                d, s = o.split("#", 1)
                norm_orig.append({"doc_id": d, "section_id": s})
        out.append({"claim_id": cid, "text": text, "orig": norm_orig})
    return out


# 第二阶段（模型写 nanopubs.json）用的条目契约：判定必须在枚举内、引文必须逐字、locator 必须来自 blocks.tsv
NANO_SPEC = {
    "name": "nanopubs",
    "list_keys": ("claims", "items", "assertions"),
    "required": ("claim", "verdict", "quote", "locator", "rationale"),
    "enums": {"verdict": ("supported", "contradicted", "insufficient")},
    "min_items": 3, "text_field": "claim", "note_field": "rationale", "note_min": 20,
    "quote_field": "quote", "locator_field": "locator", "id_field": "claim_id", "quote_min": 8,
}


def material_blocks(inp: Path, corpus: list[dict]) -> list[dict]:
    """第一阶段：能定位的块清单。有 corpus 就用 corpus 的 doc#section，否则按段落切原文文件。"""
    if corpus:
        return [{"locator": f'{s["doc_id"]}#{s["section_id"]}', "text": s["text"]} for s in corpus]
    blocks: list[dict] = []
    for p2 in sorted(inp.rglob("*")):
        if not p2.is_file() or p2.suffix.lower() not in {".md", ".txt"}:
            continue
        if any(k in p2.name.lower() for k in ("case.json", "task.md", "skill.md", "instruction")):
            continue
        text = p2.read_text(encoding="utf-8", errors="replace")
        for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
            t = norm(para)
            if len(t) >= 20:
                blocks.append({"locator": f"{p2.name}#p{pno}", "text": t})
    return blocks


def score_section(claim_units: set[str], text: str) -> float:
    su = units(text)
    if not claim_units or not su:
        return 0.0
    return len(claim_units & su) / len(claim_units)


def judge(claim: dict, corpus: list[dict]) -> dict:
    cu = units(claim["text"])
    claim_nums = {m.group(0).replace(" ", "") for m in NUM.finditer(claim["text"])}
    scored = []
    for sec in corpus:
        s = score_section(cu, sec["text"])
        scored.append((s, sec))
    scored.sort(key=lambda x: -x[0])
    orig_bad = []
    # does the report's own citation actually contain the claim?
    for o in claim["orig"]:
        sec = next((s for s in corpus if s["doc_id"] == o["doc_id"] and s["section_id"] == o["section_id"]), None)
        if sec is None:
            orig_bad.append({"ref": f'{o["doc_id"]}#{o["section_id"]}', "why": "引用段落不在证据库中"})
            continue
        cov = score_section(cu, sec["text"])
        if cov < 0.5:
            orig_bad.append({"ref": f'{o["doc_id"]}#{o["section_id"]}',
                             "why": f"该段与该观点的字面重合只有 {cov:.0%}，不是它的出处"})
    best = scored[0] if scored else (0.0, None)
    support, contra = [], []
    for s, sec in scored[:6]:
        if s < 0.5:
            continue
        # numbers must match as tokens: "5" is a substring of "55", which silently "supported" a wrong claim
        sec_nums = {m.group(0).replace(" ", "") for m in NUM.finditer(sec["text"])}
        has_num = (claim_nums <= sec_nums) if claim_nums else True
        num_conflict = bool(claim_nums) and not (claim_nums & sec_nums)
        negated = bool(NEG.search(sec["text"]))
        if sec["superseded"]:
            contra.append((s, sec, "该段所在文档已被修订/撤稿，不能作为支持证据"))
            continue
        if has_num and claim_nums:
            support.append((s, sec, "关键数值与该段完全一致"))
        elif not claim_nums and s >= 0.6 and not negated:   # text-only claims may be supported by wording
            support.append((s, sec, "字面重合高且无否定表述"))
        elif num_conflict and s >= 0.5:
            contra.append((s, sec, f'证据给出的数值 {sorted(sec_nums)[:3]} 与观点 {sorted(claim_nums)[:3]} 不一致'))
        elif negated and s >= 0.5:
            contra.append((s, sec, "该段以否定表述描述同一对象"))
    active_support = [(sc, sec, why) for sc, sec, why in support]
    # cite the live contradiction first: a superseded section explains why the claim fails, not what refutes it
    contra.sort(key=lambda x: (x[1]["superseded"], -x[0]))
    if active_support:
        status = "supported"
        ev = active_support[:2]
        note = (f'证据段 {ev[0][1]["doc_id"]}#{ev[0][1]["section_id"]} 与观点的字面重合 '
                f'{ev[0][0]:.0%}，' + ("数值与条件一致，且该段无否定表述。" if claim_nums
                                      else "表述一致，且该段无否定表述。") +
                (f' 原引用 {orig_bad[0]["ref"]} 与该段不一致，已改用正确出处。' if orig_bad else ""))
    elif contra:
        status = "contradicted"
        ev = contra[:2]
        note = (f'证据段 {ev[0][1]["doc_id"]}#{ev[0][1]["section_id"]} 与观点冲突：{ev[0][2]}'
                f'（字面重合 {ev[0][0]:.0%}）。')
    else:
        status = "insufficient"
        ev = []
        top = best[1]
        note = ("证据库中没有段落能同时覆盖该观点的主语与关键数值"
                + (f'（最高重合 {best[0]:.0%}：{top["doc_id"]}#{top["section_id"]}）' if top else "")
                + "，因此不足以判定，也不引用不相关的段落。")
    return {"claim_id": claim["claim_id"], "status": status,
            "evidence": [{"doc_id": s["doc_id"], "section_id": s["section_id"],
                          "quote": s["text"][:300], "overlap": round(sc, 2)}
                         for sc, s, _ in ev],
            "audit_note": note[:1900],
            "original_citation_problem": orig_bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--blocks", action="store_true", help="只写 blocks.tsv（模型写作的唯一定位来源）")
    ap.add_argument("--verify-nanopubs", default=None, help="校验模型交回的 nanopubs.json")
    ap.add_argument("--run-all", action="store_true",
                    help="一次性：blocks.tsv → 有 nanopubs.json 且过校验就用它 → 出审计 → attach_meta")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    corpus = load_corpus(inp)
    blocks = material_blocks(inp, corpus)
    model_items: list[dict] = []
    nano_report: dict = {}
    if args.blocks:
        n = dump_blocks(blocks, out)
        print(json.dumps({"case_id": case_id, "blocks": n, "blocks_tsv": str(out / "blocks.tsv")},
                         ensure_ascii=False))
        return 0
    if args.verify_nanopubs:
        items, rep, ok = author_run(Path(args.verify_nanopubs), blocks, out, NANO_SPEC)
        print(json.dumps(rep, ensure_ascii=False))
        return 0 if ok else 1
    if args.run_all:
        dump_blocks(blocks, out)
        cand = out / "nanopubs.json"
        if cand.is_file():
            model_items, nano_report, ok = author_run(cand, blocks, out, NANO_SPEC)
            if not ok:
                model_items = []
    claims = load_claims(inp)
    if model_items:
        claims = [{"claim_id": str(it.get("claim_id") or f"N{i:02d}"),
                   "text": norm(it.get("claim")),
                   "orig": [],
                   "_locator": str(it.get("locator") or ""),
                   "_quote": str(it.get("quote") or ""),
                   "_verdict": str(it.get("verdict") or ""),
                   "_rationale": norm(it.get("rationale") or "")}
                  for i, it in enumerate(model_items, 1)]
    derived_from_text = False
    if not claims:
        # no explicit claim list: extract assertion sentences from the material as the nanopublication set
        # 断言句的判据：有结论性提示词，或有量值 + 比较/变化谓词（"寿命明显更高"、"从 1.3% 降到 1.0%"）
        pat = re.compile(r"(表明|说明|显示|发现|证实|提示|因此|可见|结果表明|结论|"
                         r"显著|提高|提升|降低|减少|增加|下降|更高|更低|优于|差异|"
                         r"we (?:find|show|observe)|suggest\w*|indicat\w*|improv\w*|reduc\w*|"
                         r"increas\w*|decreas\w*|significant\w*)", re.I)
        texts = []
        for p2 in sorted(inp.rglob("*")):
            if p2.is_file() and p2.suffix.lower() in {".md", ".txt"}:
                texts.append((p2.name, p2.read_text(encoding="utf-8", errors="replace")))
        idx = 0
        for name, text in texts:
            for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
                para = re.sub(r"^\s*#{1,6}\s*", "", norm(para))       # a heading prefix is not part of a claim
                for sent in re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", para):
                    t = norm(sent)
                    # a Chinese assertion is complete well before 18 characters of latin
                    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
                    has_num = bool(NUM.search(t))
                    # a protocol sentence ("was measured by…") is not a claim: keep it only when nothing else
                    # in the document asserts anything, so the nanopublication list stays about findings.
                    protocol = re.search(r"\b(?:(?:was|were|we)\s+(?:measured|performed|printed|prepared|recorded|"
                                         r"collected|randomised|randomized|tested))\b|采用|测量|打印|制备|测试", t, re.I)
                    # a section word glued to the paragraph ("Results Porosity decreased…") is a heading artifact
                    t = re.sub(r"^(Abstract|Introduction|Methods|Results|Discussion|Conclusions?|Limitations|"
                               r"摘要|引言|方法|结果|讨论|结论|局限)\s+(?=[A-Z\u4e00-\u9fff])", "", t)
                    if protocol and not re.search(r"(decreas|increas|improv|reduc|higher|lower|significant|"
                                                  r"降低|提高|显著|差异)", t, re.I):
                        continue
                    if len(t) >= (12 if cjk >= 8 else 18) and (pat.search(t) or has_num):
                        idx += 1
                        claims.append({"claim_id": f"N{idx:02d}", "text": t[:300], "orig": [],
                                       "_locator": f"{name}#p{pno}", "_quote": t[:300]})
        derived_from_text = bool(claims)
    if not claims:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["输入里没有 claims.json（观点清单）"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    audited = [judge(c, corpus) for c in claims]
    corpus_refs = {(s["doc_id"], s["section_id"]) for s in corpus}
    for a, c in zip(audited, claims):
        if c.get("_locator"):
            a["locator"] = c["_locator"]
        if c.get("_quote"):
            a["quote"] = c["_quote"]
        a["claim"] = c["text"]
        if c.get("_verdict"):
            # 模型给了判定：用它，但把脚本的字面判定一并留作对照（check_report 里给一致率）
            a["script_status"] = a["status"]
            a["status"] = c["_verdict"]
            a["audit_note"] = c["_rationale"] or a["audit_note"]
            a["judged_by"] = "model"
            if "#" in c["_locator"]:
                d, sec = c["_locator"].split("#", 1)
                if (d, sec) in corpus_refs:
                    a["evidence"] = [{"doc_id": d, "section_id": sec}]
    audit = {"case_id": case_id, "claims": audited}
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 观点证据审计（annotated report）", ""]
    for a in audited:
        refs = ", ".join(f'{e["doc_id"]}#{e["section_id"]}' for e in a["evidence"]) or "无有效证据"
        lines.append(f'{a["claim_id"]} | {a["status"]} | {refs}')
    (out / "annotated_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # self-checks against the task's own rules
    ids = [a["claim_id"] for a in audited]
    corpus_refs = {(s["doc_id"], s["section_id"]) for s in corpus}
    checks = {
        "ids_unique": len(ids) == len(set(ids)),
        "ids_complete": len(ids) == len(claims),
        "status_enum_only": all(a["status"] in ("supported", "contradicted", "insufficient") for a in audited),
        "evidence_resolvable": all((e["doc_id"], e["section_id"]) in corpus_refs
                                   for a in audited for e in a["evidence"]),
        "audit_note_nonempty": all(a["audit_note"].strip() for a in audited),
        "report_matches_json": all(
            line.split(" | ")[1] == a["status"] and line.split(" | ")[0] == a["claim_id"]
            for line, a in zip(lines[2:], audited)),
        "no_superseded_as_support": all(
            not any(s["superseded"] for s in corpus
                    if (s["doc_id"], s["section_id"]) in {(e["doc_id"], e["section_id"]) for e in a["evidence"]})
            or a["status"] != "supported" for a in audited),
    }
    if model_items:
        mchk = nano_report.get("checks", {})
        checks["model_quotes_verbatim"] = bool(mchk.get("quotes_verbatim"))
        checks["model_locators_from_stage1"] = bool(mchk.get("locators_from_stage1"))
        agree = sum(1 for a in audited if a.get("script_status") == a["status"])
        checks["model_script_agreement_rate"] = round(agree / max(len(audited), 1), 3)
    # 一致率是给读者看的对照指标，不是门禁：模型判 supported 而字面重合低，可能是它读懂了论证
    checks["all_passed"] = all(v for k, v in checks.items() if k != "model_script_agreement_rate")
    (out / "check_report.json").write_text(json.dumps(
        {"case_id": case_id, "checks": checks, "claims": len(audited),
         "by_status": {k: sum(1 for a in audited if a["status"] == k)
                       for k in ("supported", "contradicted", "insufficient")}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    payload = {"case_id": case_id, "status": "answered", "authored_by": "model" if model_items else "script",
               "audit": {"json": "audit.json", "report": "annotated_report.md",
                         "claims": len(audited),
                         "by_status": {k: sum(1 for a in audited if a["status"] == k)
                                       for k in ("supported", "contradicted", "insufficient")},
                         "original_citations_challenged": sum(
                             1 for a in audited if a["original_citation_problem"])},
               "checks": "check_report.json",
               "gaps": ([] if corpus else ["没有可读的 corpus.json，所有观点只能判 insufficient"])
                       + (["输入未提供 claims.json，断言由材料中的结论句提取（已标 locator）"] if derived_from_text else [])
                       + ([] if model_items or not args.run_all else
                          ["nanopubs.json 缺失或未通过门禁，已退回脚本推导断言"]),
               "limitations": ["判定基于字面重合与数值一致，不做语义推理",
                               "被修订/撤稿的段落只用于反驳，不用于支持",
                               "证据只引用整段原文，不做改写（改写会导致逐字核对失败）"],
               "verification": checks}
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "claims": len(audited),
                      "by_status": payload["audit"]["by_status"],
                      "authored_by": payload["authored_by"],
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    if args.run_all:
        meta = Path(__file__).resolve().parent / "attach_meta.py"
        if meta.is_file():
            subprocess.run([sys.executable, str(meta), "--input", str(inp), "--output", str(out)],
                           capture_output=True, text=True, timeout=300)
    return 0


if __name__ == "__main__":
    sys.exit(main())
