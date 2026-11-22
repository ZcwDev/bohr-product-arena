#!/usr/bin/env python3
"""Material -> section plan -> locator-bound facts -> assembled draft scaffold.

The other scripts answer "is the result well-formed and traceable". This one
answers "where does the draft come from". It never writes prose from memory:
every sentence it puts in the draft is copied verbatim from a block that carries
a locator, and every section it cannot fill is marked `not_reported` with the
reason, so the agent knows exactly which paragraphs to write by hand.

Three stages, each a separate subcommand, each writing one JSON/text artifact:

  plan      case.json                       -> plan.json   (section skeleton)
  facts     blocks.json                     -> facts.jsonl (numbers/terms/conditions + locator)
  assemble  plan.json + blocks.json + facts -> draft.md + spec.json (docx/preview input)

Locators are whatever `make_blocks.py` / `pdf_text.py` produced for the block;
this script does not invent or rewrite them.

CLI:
  python3 paper.py plan     /input/case.json -o /app/work/plan.json
  python3 paper.py facts    /app/work/blocks.json -o /app/work/facts.jsonl
  python3 paper.py assemble /app/work/plan.json --blocks /app/work/blocks.json \
                            --facts /app/work/facts.jsonl \
                            -o /app/work/draft.md --spec /app/work/export/spec.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence as evidence_mod  # noqa: E402

# (id, heading, purpose, evidence_need, word_budget)
# evidence_need drives which blocks are candidates for the section.
TEMPLATES = {
    "research": [
        ("abstract", "Abstract", "问题、方法、主要结果与结论各一句", "result", 250),
        ("introduction", "1 Introduction", "研究背景与未解决问题", "background", 800),
        ("methods", "2 Methods", "材料、样本量、条件与统计方法", "method", 900),
        ("results", "3 Results", "按图表顺序陈述结果，数值带单位与不确定性", "result", 1000),
        ("discussion", "4 Discussion", "结果解释、与既有工作比较、限制", "discussion", 900),
        ("conclusion", "5 Conclusion", "可被材料支持的结论，不外推", "discussion", 250),
        ("references", "References", "正文引用编号与条目一一对应", "citation", 0),
        ("data_availability", "Data availability", "数据可得性声明", "none", 80),
    ],
    "review": [
        ("abstract", "Abstract", "综述范围与主要论点", "background", 250),
        ("introduction", "1 Introduction", "领域现状与综述动机", "background", 700),
        ("themes", "2 Thematic synthesis", "按主题组织证据，逐条给出处", "result", 1800),
        ("synthesis", "3 Synthesis and open questions", "跨研究的共识、冲突与空白", "discussion", 800),
        ("conclusion", "4 Conclusion", "不外推的总结", "discussion", 250),
        ("references", "References", "条目与正文引用一致", "citation", 0),
    ],
    "letter": [
        ("abstract", "Abstract", "一段式摘要", "result", 150),
        ("main", "Main text", "问题—方法—结果—含义", "result", 1200),
        ("references", "References", "条目与正文引用一致", "citation", 0),
    ],
    "thesis": [
        ("introduction", "Chapter 1 Introduction", "背景、问题与目标", "background", 900),
        ("literature", "Chapter 2 Literature review", "既有工作与缺口", "background", 1200),
        ("methods", "Chapter 3 Methods", "设计与步骤", "method", 1000),
        ("results", "Chapter 4 Results", "结果与图表", "result", 1200),
        ("discussion", "Chapter 5 Discussion", "解释与限制", "discussion", 900),
        ("conclusion", "Chapter 6 Conclusion", "结论与后续工作", "discussion", 300),
        ("references", "References", "条目与正文引用一致", "citation", 0),
    ],
}

GENRE_WORDS = {
    "research": ("research article", "original article", "研究论文", "论著", "imrad", "article"),
    "review": ("review", "综述", "survey", "meta-analysis"),
    "letter": ("letter", "communication", "简报", "快报", "short"),
    "thesis": ("thesis", "dissertation", "学位论文", "毕业设计", "chapter"),
}

NEED_CUES = {
    "background": re.compile(
        r"\b(?:previous(?:ly)?|prior|earlier|has been|have been|remains?|unknown|unclear|"
        r"little is known|reported that|established|widely used|background)\b|"
        r"已有|此前|尚未|未知|不清楚|背景|报道", re.I),
    "method": re.compile(
        r"\b(?:we (?:used|performed|measured|collected|recruited|conducted)|protocol|"
        r"sample[sd]?|reagent|incubat\w+|centrifug\w+|randomi[sz]\w+|statistical|"
        r"two-tailed|software|version|°C|ml|mmol|l\b)\b|"
        r"方法|样本|试剂|孵育|离心|随机|统计|软件", re.I),
    "result": re.compile(
        r"\b(?:we (?:found|observed|measured|obtained)|result\w*|increased|decreased|"
        r"compared (?:to|with)|no significant|significant\w*|mean|median|±|\bn\s*=\s*\d+)\b|"
        r"结果|观察到|显著|平均|中位数", re.I),
    "discussion": re.compile(
        r"\b(?:suggest\w*|indicat\w*|consistent with|in contrast|however|limitation\w*|"
        r"these (?:results|findings)|in line with|caveat)\b|"
        r"提示|表明|一致|然而|局限|本研究", re.I),
    "citation": re.compile(r"\[\d+(?:\s*[-–,]\s*\d+)*\]|\\cite[tp]?\{|\(\w[\w\-]+ et al\.,? \d{4}\)"),
    "none": re.compile(r"(?!x)x"),
}

SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+(?=[A-Z一-鿿(\[])")


def _sentences(text: str, min_chars: int = 24) -> list[str]:
    out = []
    for raw in SENT_SPLIT.split(text or ""):
        s = (raw or "").strip()
        if len(re.sub(r"\s", "", s)) >= min_chars:
            out.append(s)
    return out


def detect_genre(case: dict) -> tuple[str, str]:
    """Return (genre, where it came from). Never guesses silently."""
    for key in ("genre", "genre_hint", "文体", "doc_type", "article_type", "task_type"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            lowered = val.strip().lower()
            for genre, words in GENRE_WORDS.items():
                if any(w in lowered for w in words):
                    return genre, f"case.{key}"
            return "research", f"case.{key} (unrecognised value {val!r}; fell back to research)"
    blob = " ".join(str(case.get(k) or "") for k in
                    ("task_instruction", "instruction", "task", "title", "target_journal")).lower()
    for genre, words in GENRE_WORDS.items():
        if any(w in blob for w in words):
            return genre, "inferred from task text"
    return "research", "default (no genre field in case.json and no cue in the task text)"


def cmd_plan(args):
    case = json.load(open(args.case, encoding="utf-8"))
    genre, source = detect_genre(case)
    explicit = case.get("sections") or case.get("required_sections") or case.get("章节")
    if isinstance(explicit, list) and explicit:
        sections = []
        for i, item in enumerate(explicit, start=1):
            if isinstance(item, dict):
                sections.append({"id": item.get("id") or f"s{i}",
                                 "heading": item.get("heading") or item.get("title") or f"Section {i}",
                                 "purpose": item.get("purpose") or "as required by the case",
                                 "evidence_need": item.get("evidence_need") or "result",
                                 "word_budget": int(item.get("word_budget") or 0)})
            else:
                sections.append({"id": f"s{i}", "heading": str(item),
                                 "purpose": "as required by the case",
                                 "evidence_need": "result", "word_budget": 0})
        sections_source = "case.sections (used verbatim)"
    else:
        sections = [{"id": sid, "heading": h, "purpose": p, "evidence_need": n, "word_budget": w}
                    for sid, h, p, n, w in TEMPLATES[genre]]
        sections_source = f"built-in template for genre {genre!r}"

    missing = []
    if not str(case.get("case_id") or "").strip():
        missing.append("case_id absent: stop and report input_error rather than inventing an id")
    if not str(case.get("task_instruction") or case.get("instruction") or "").strip():
        missing.append("task_instruction absent: the genre/audience/format constraints are unknown")
    if str(case.get("case_id") or "").startswith("EXAMPLE"):
        missing.append("case_id is the published example id: this plan is an interface demo, "
                       "not a plan for a real case")

    plan = {"case_id": case.get("case_id"), "genre": genre, "genre_source": source,
            "sections_source": sections_source,
            "target_journal": case.get("target_journal") or case.get("journal") or "not_reported",
            "audience": case.get("audience") or "not_reported",
            "word_limit": case.get("word_limit") or case.get("字数") or "not_reported",
            "citation_style": case.get("citation_style") or case.get("引用格式") or "not_reported",
            "sections": sections,
            "missing_inputs": missing or ["none: every field this stage needs was present"]}

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    json.dump(plan, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    empty = [s["id"] for s in sections if s["evidence_need"] != "none"]
    print(json.dumps({"out": args.out, "case_id": plan["case_id"], "genre": genre,
                      "genre_source": source, "sections": len(sections),
                      "need_evidence": empty, "missing_inputs": plan["missing_inputs"]},
                     ensure_ascii=False))
    return 0


def cmd_facts(args):
    blocks = []
    for path in args.blocks:
        data = json.load(open(path, encoding="utf-8"))
        blocks.extend(data.get("blocks") if isinstance(data, dict) else data)
    rows, seen = [], set()
    for block in blocks or []:
        text = (block.get("text") or "").strip()
        loc = block.get("locator")
        if not text or not loc:
            continue
        for sent in _sentences(text):
            numbers = [m.group(0).strip() for m in evidence_mod.NUMBER_RE.finditer(sent)]
            has_cond = bool(re.search(r"\b(?:if|when|unless|at|after|before|during|under)\b|"
                                      r"当|若|在.{0,6}条件下", sent, re.I))
            has_cite = bool(NEED_CUES["citation"].search(sent))
            kinds = []
            if numbers:
                kinds.append("number")
            if has_cond:
                kinds.append("condition")
            if has_cite:
                kinds.append("citation")
            if not kinds:
                continue
            key = (loc, sent[:80])
            if key in seen:
                continue
            seen.add(key)
            rows.append({"kind": "+".join(kinds), "quote": sent, "locator": loc,
                         "numbers": numbers, "block_type": block.get("type") or "body"})

    with open(args.out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    by_kind = {}
    for row in rows:
        for k in row["kind"].split("+"):
            by_kind[k] = by_kind.get(k, 0) + 1
    print(json.dumps({"out": args.out, "blocks": len(blocks or []), "facts": len(rows),
                      "by_kind": by_kind,
                      "numbers": sorted({n for row in rows for n in row["numbers"]})[:30]},
                     ensure_ascii=False))
    return 0


def _cue(regex: str) -> re.Pattern:
    return re.compile(regex, re.I)


# A methods sentence inside a Results section is the classic wrong-section error.
# Positive cues alone cannot see it, so each need also carries what rules a block out.
NEG_CUES = {
    "result": _cue(r"\b(?:cells? were|were (?:cultured|incubated|randomi[sz]ed|divided)|"
                   r"protocol|reagents?|medium|statistical|software|version|"
                   r"t-test|two-tailed|threshold|prior (?:studies|work)|previously|"
                   r"has been reported|other (?:studies|work)|reported that)\b|"
                   r"培养|孵育条件|随机化由|试剂|培养基|统计采用|软件为|阈值设为|"
                   r"此前研究|先前研究|已有工作|另有工作|另有研究|报道|"
                   r"尚不清楚|然而|提示|可能|培养|培养基|胎牛血清|溶剂|给予"),
    "method": _cue(r"\b(?:suggests?|indicates?|these (?:results|findings)|consistent with|"
                   r"in contrast|however|limitation|was (?:measured|observed) at)\b|"
                   r"提示|表明|然而|局限|浓度为|无统计学意义|与对照组相比"),
    "background": _cue(r"\b(?:cells? were|incubated|randomi[sz]ed|t-test|software)\b|"
                       r"孵育|随机化由|统计采用|软件为"),
    "discussion": _cue(r"\b(?:protocol|reagents?|statistical|software|t-test)\b|"
                       r"试剂|统计采用|软件为"),
}


# Task instructions and runtime contracts are prose that reads like material.
# A harness that globs `*.md` sweeps task.md and the output contract into the
# block list, and sentences such as "do not invent benchmark scores" then land
# in the Abstract. Excluding them is the safe direction: an excluded block makes
# its section `not_reported`, which a human sees, while an included one silently
# becomes fake manuscript content.
INSTRUCTION_CUES = re.compile(
    r"do not (?:invent|fabricate|submit|include)|the evaluator|at execution time|"
    r"model requests|per response|actual native traces|benchmark scores|"
    r"symlinks|utf-8 text files|runtime contract|bundled files|resource entry point|"
    r"参赛|题面|评分维度|评分点|交付要求|提交材料|赛事|评审|本 skill|本包|"
    r"安装方式|调用方式|输入输出", re.I)


def _is_instruction(block_text: str) -> bool:
    return bool(INSTRUCTION_CUES.search(block_text or ""))


def _score(block_text: str, need: str) -> int:
    cue = NEED_CUES.get(need) or NEED_CUES["result"]
    hits = len(cue.findall(block_text))
    if need == "result":
        hits += 2 * len(evidence_mod.NUMBER_RE.findall(block_text))
    if need == "method":
        hits += len(re.findall(r"\bn\s*=\s*\d+", block_text))
    neg = NEG_CUES.get(need)
    if neg is not None:
        hits -= 3 * len(neg.findall(block_text))
    return hits


def cmd_assemble(args):
    plan = json.load(open(args.plan, encoding="utf-8"))
    blocks, seen_blocks = [], set()
    for path in args.blocks:
        bdata = json.load(open(path, encoding="utf-8"))
        for b in (bdata.get("blocks") if isinstance(bdata, dict) else bdata) or []:
            key = (b.get("locator"), (b.get("text") or "")[:120])
            if key in seen_blocks:
                continue
            seen_blocks.add(key)
            blocks.append(b)
    facts = []
    if args.facts and os.path.isfile(args.facts):
        facts = [json.loads(line) for line in open(args.facts, encoding="utf-8") if line.strip()]

    candidates = [b for b in (blocks or []) if (b.get("text") or "").strip() and b.get("locator")]
    usable = [b for b in candidates if not _is_instruction(b["text"])]
    excluded = [b for b in candidates if _is_instruction(b["text"])]
    used_count = {id(b): 0 for b in usable}
    sections_out, gaps, index_rows = [], [], []

    for sec in plan["sections"]:
        need = sec.get("evidence_need") or "result"
        picked = []
        if need != "none":
            ranked = sorted(usable, key=lambda b: (-_score(b["text"], need), b["locator"]))
            for block in ranked:
                if used_count[id(block)] >= 2:
                    continue
                if _score(block["text"], need) <= 0:
                    break
                picked.append(block)
                used_count[id(block)] += 1
                if len(picked) >= args.max_blocks:
                    break
        if picked:
            lines = []
            for block in picked:
                sents = _sentences(block["text"]) or [block["text"].strip()]
                for sent in sents[: args.max_sentences]:
                    lines.append(f"- {sent} ⟨{block['locator']}⟩")
                    index_rows.append((block["locator"], sec["heading"], sent[:60]))
            body = "\n".join(lines)
            status = "material"
        else:
            why = ("本节要求的是引用条目，材料里没有任何编号引用；需人工按目标期刊格式补齐"
                   if need == "citation" else
                   f"材料里没有与该节（{need}）匹配的可用片段；不写内容，留给人工")
            body = f"> **not_reported** — {why}。"
            gaps.append({"section": sec["id"], "heading": sec["heading"], "reason": why})
            status = "not_reported"
        sections_out.append({"id": sec["id"], "heading": sec["heading"], "status": status,
                             "purpose": sec.get("purpose"), "blocks": [b["locator"] for b in picked],
                             "body": body})

    title = plan.get("title") or plan.get("case_id") or "Draft"
    md = [f"# Draft — {title}", "",
          "> 由 `paper.py assemble` 生成。每条证据句都是材料原文逐字复制，末尾 ⟨…⟩ 是它在材料里的定位符。",
          "> 标记 `not_reported` 的小节材料里没有可用内容，需要人工撰写；本文件不是成稿。", ""]
    for i, sec in enumerate(sections_out, start=1):
        md.append(f"## {sec['heading']}")
        md.append("")
        md.append(sec["body"])
        md.append("")
    md.append("## 材料索引（定位符 → 支撑小节）")
    md.append("")
    md.append("| 定位符 | 支撑小节 | 片段 |")
    md.append("|---|---|---|")
    for loc, heading, snippet in index_rows:
        md.append(f"| `{loc}` | {heading} | {snippet.replace('|', '/')} |")
    md.append("")
    md.append("## 排除的指令类文本（不是研究材料）")
    md.append("")
    if excluded:
        md.append("| 定位符 | 片段 | 排除原因 |")
        md.append("|---|---|---|")
        for b in excluded:
            snippet = re.sub(r"\s+", " ", b["text"] or "")[:70].replace("|", "/")
            md.append(f"| `{b['locator']}` | {snippet} | "
                      f"命中指令/运行时契约用词，疑似题面而非材料 |")
    else:
        md.append("无。")
    md.append("")
    md.append("## 缺项（not_reported）")
    md.append("")
    if gaps:
        md.append("| 小节 | 原因 |")
        md.append("|---|---|")
        for g in gaps:
            md.append(f"| {g['heading']} | {g['reason']} |")
    else:
        md.append("无：每个小节都拿到了材料片段。这不等于内容正确，只说明有材料可引。")
    md.append("")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    open(args.out, "w", encoding="utf-8").write("\n".join(md))

    # Build rich spec blocks that exercise all OOXML features
    spec_blocks = []

    # 1. Table of contents placeholder
    spec_blocks.append({"type": "toc"})

    for sec in sections_out:
        # H1 heading
        spec_blocks.append({"type": "h1", "text": sec["heading"]})

        if sec["status"] == "material":
            # Parse the body into evidence lines (bulleted) and render as bullet list
            evidence_lines = []
            for line in sec["body"].split("\n"):
                line = line.strip()
                if line.startswith("- "):
                    evidence_lines.append(line[2:])
            if evidence_lines:
                for ev in evidence_lines:
                    spec_blocks.append({"type": "bullet", "text": ev})
            else:
                spec_blocks.append({"type": "p", "text": sec["body"]})
        elif sec["status"] == "not_reported":
            # Show revision marks for gaps (tracked changes)
            spec_blocks.append({"type": "revision_del", "text": f"[{sec['heading']}: 材料不足，待人工补充]",
                                "author": "Skill Reviewer"})
            spec_blocks.append({"type": "revision_ins", "text": f"[待人工撰写: {sec['heading']}]",
                                "author": "Skill Reviewer"})

    # Build a summary table from index rows
    if index_rows:
        table_rows = [["定位符", "支撑小节", "片段"]]
        for loc, heading, snippet in index_rows[:8]:
            table_rows.append([loc, heading, snippet[:40]])
        spec_blocks.append({"type": "h2", "text": "材料索引表"})
        spec_blocks.append({"type": "table", "rows": table_rows})

    # Add numbered list for methodology/key steps if we have enough sections
    if len(sections_out) >= 3:
        spec_blocks.append({"type": "h2", "text": "章节结构"})
        for i, sec in enumerate(sections_out, 1):
            status_label = "✓" if sec["status"] == "material" else "○"
            spec_blocks.append({"type": "number", "text": f"{status_label} {sec['heading']}"})

    # Build abstract from first filled section
    abstract_text = ""
    for sec in sections_out:
        if sec["status"] == "material":
            abstract_text = sec["body"][:300].replace("\n", " ")
            break

    spec = {
        "title": f"Draft — {title}",
        "author": "Generated by paper.py assemble",
        "abstract": abstract_text or "Draft generated from indexed materials.",
        "toc": True,
        "blocks": spec_blocks,
    }
    if args.spec:
        os.makedirs(os.path.dirname(os.path.abspath(args.spec)) or ".", exist_ok=True)
        json.dump(spec, open(args.spec, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    summary = {"out": args.out, "sections": len(sections_out),
               "filled": sum(1 for s in sections_out if s["status"] == "material"),
               "not_reported": [g["heading"] for g in gaps],
               "locators_used": len(index_rows),
               "facts_available": len(facts),
               "instruction_blocks_excluded": len(excluded),
               "spec": args.spec or None}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Material -> plan -> facts -> draft scaffold")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan"); p.add_argument("case"); p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_plan)

    f = sub.add_parser("facts"); f.add_argument("blocks", nargs="+"); f.add_argument("-o", "--out", required=True)
    f.set_defaults(fn=cmd_facts)

    a = sub.add_parser("assemble")
    a.add_argument("plan"); a.add_argument("--blocks", required=True, nargs="+",
                                          help="one or more blocks.json files")
    a.add_argument("--facts", default=None)
    a.add_argument("-o", "--out", required=True)
    a.add_argument("--spec", default=None, help="also write the ooxml.py/preview.py render spec")
    a.add_argument("--max-blocks", type=int, default=3, dest="max_blocks")
    a.add_argument("--max-sentences", type=int, default=2, dest="max_sentences")
    a.set_defaults(fn=cmd_assemble)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
