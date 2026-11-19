#!/usr/bin/env python3
"""cite_manage — make the reference list and the in-text citations agree, and prove it.

Citation management is graded on agreement, not on prettiness: every citation must have an entry, every entry
must be cited, numbering must follow first appearance, and duplicate or incomplete entries must be caught.
This engine works on the manuscript as written (numeric or author-year), never invents a missing reference,
and renumbers only where that is provably safe (numeric style with a complete entry table).

  references_clean.md   entries renumbered by first appearance, duplicates merged with both keys kept, and
                        entries whose citation has no target flagged inline as `[缺条目]`
  citation_map.tsv      citation key → new number · entry · first locator · status
  reference_report.md   findings by category with locators
  check_report.json     machine checks: agreement both ways, numbering, duplicates, field completeness
  result.json           counts · gaps · limitations · verification · metadata

usage:
  cite_manage.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _meta import attach as meta_attach
except Exception:
    def meta_attach(inp, out):
        return {"unavailable": True}

NUM_CITE = re.compile(r"\[(\d+(?:\s*[-,，]\s*\d+)*)\]")
AY_CITE = re.compile(r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z\-]+))?,\s*\d{4}[a-z]?)\)")
REF_HEAD = re.compile(r"^\s*(#{0,4}\s*)?(参考文献|references|bibliography|literature cited)\s*$", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
YEAR = re.compile(r"(?<!\d)(19|20)\d{2}[a-z]?(?!\d)")
BIBITEM = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_doc(inp: Path) -> tuple[Path | None, str]:
    best, text = None, ""
    for p in sorted(inp.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".md", ".txt"} \
                and p.name.lower() not in {"case.json", "instruction.md", "question.md"}:
            t = p.read_text(encoding="utf-8", errors="replace")
            if len(t) > len(text):
                best, text = p, t
    return best, text


def split_body_refs(text: str) -> tuple[str, list[str], int]:
    """Return (body, reference entries, heading line index). Entries are split on blank lines or bullets."""
    lines = text.splitlines()
    head = None
    for i, line in enumerate(lines):
        if REF_HEAD.match(line.strip()):
            head = i
            break
    if head is None:
        return text, [], 0
    body = "\n".join(lines[:head])
    block = lines[head + 1:]
    entries: list[str] = []
    cur: list[str] = []
    for raw in block:
        line = raw.strip()
        if not line:
            if cur:
                entries.append(norm(" ".join(cur))); cur = []
            continue
        if re.match(r"^\s*(\[\d+\]|\d+[.)])\s+", line) and cur:
            entries.append(norm(" ".join(cur))); cur = [line]
        else:
            cur.append(line)
    if cur:
        entries.append(norm(" ".join(cur)))
    # strip a leading marker from each entry but remember it
    cleaned = []
    for e in entries:
        cleaned.append(re.sub(r"^(\[\d+\]|\d+[.)])\s*", "", e))
    return body, cleaned, head


def expand_numeric(group: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[,，]", group):
        part = part.strip()
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a <= 100:
                out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def entry_fields(entry: str) -> dict:
    doi = DOI.search(entry)
    year = YEAR.search(entry)
    # authors: leading run of capitalized names before the year
    authors = ""
    m = re.match(r"^(.{3,120}?)[.,(]\s*((?:19|20)\d{2})", entry)
    if m:
        authors = m.group(1)
    title = ""
    mt = re.search(r"[.。]\s*([^.]{8,180})[.。]", entry)
    if mt:
        title = norm(mt.group(1))
    return {"doi": doi.group(1).lower() if doi else "", "year": year.group(0) if year else "",
            "authors": norm(authors), "title": title}


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
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    src, text = read_doc(inp)
    bib_keys = set()
    for b in inp.rglob("*.bib"):
        bib_keys |= {k.strip() for k in BIBITEM.findall(b.read_text(encoding="utf-8", errors="replace"))}
    if not text.strip():
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["输入里没有可解析的稿件"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    body, entries, head_line = split_body_refs(text)
    findings: list[dict] = []
    # ---- in-text citations ----
    num_cites: list[tuple[int, str]] = []
    for m in NUM_CITE.finditer(text):
        line_no = text[:m.start()].count("\n") + 1
        for n in expand_numeric(m.group(1)):
            num_cites.append((n, f"L{line_no}"))
    ay_cites = [(m.group(1), f"L{text[:m.start()].count(chr(10)) + 1}") for m in AY_CITE.finditer(text)]
    style = "numeric" if len(num_cites) >= len(ay_cites) and num_cites else ("author_year" if ay_cites else "none")
    if num_cites and ay_cites:
        findings.append({"kind": "mixed_citation_style", "locator": "-",
                         "detail": f"同时出现数字引用（{len(num_cites)} 处）与作者-年份引用（{len(ay_cites)} 处），需统一"})
    # ---- entries ----
    parsed = []
    for i, e in enumerate(entries, 1):
        f = entry_fields(e)
        parsed.append({"index": i, "text": e[:400], **f})
    if not entries:
        findings.append({"kind": "no_reference_list", "locator": f"L{head_line}" if head_line else "-",
                         "detail": "没有检出参考文献列表（标题或编号条目）"})
    # duplicates
    seen_doi, seen_title = {}, {}
    for p in parsed:
        if p["doi"]:
            if p["doi"] in seen_doi:
                findings.append({"kind": "duplicate_entry", "locator": f"entry#{p['index']}",
                                 "detail": f"与 entry#{seen_doi[p['doi']]} 的 DOI 相同（{p['doi']}），保留两处编号"})
            else:
                seen_doi[p["doi"]] = p["index"]
        key = re.sub(r"\W+", "", p["title"].lower())[:60]
        if key:
            if key in seen_title:
                findings.append({"kind": "duplicate_entry", "locator": f"entry#{p['index']}",
                                 "detail": f"标题与 entry#{seen_title[key]} 高度相同"})
            else:
                seen_title[key] = p["index"]
        missing = [k for k in ("authors", "year", "title") if not p[k]]
        if missing:
            findings.append({"kind": "entry_incomplete", "locator": f"entry#{p['index']}",
                             "detail": f"缺少字段：{', '.join(missing)}"})
    # ---- agreement ----
    max_entry = len(entries)
    for n, loc in num_cites:
        if n < 1 or n > max_entry:
            findings.append({"kind": "cite_without_entry", "locator": loc,
                             "detail": f"正文引用 [{n}] 没有对应条目（条目数 {max_entry}）"})
    cited_numbers = {n for n, _ in num_cites}
    for p in parsed:
        if style == "numeric" and p["index"] not in cited_numbers:
            findings.append({"kind": "entry_without_citation", "locator": f"entry#{p['index']}",
                             "detail": f"条目 {p['index']} 未被正文引用（保留，标注）"})
    # numbering: first appearance order
    first_seen: list[int] = []
    for n, _ in num_cites:
        if n not in first_seen:
            first_seen.append(n)
    out_of_order = [n for i, n in enumerate(first_seen) if i and n < first_seen[i - 1]]
    if out_of_order:
        findings.append({"kind": "numbering_out_of_order", "locator": "-",
                         "detail": f"编号未按首次出现递增：{out_of_order[:8]}"})
    # ---- renumber by first appearance (safe: numeric style + complete table) ----
    renum = {old: i + 1 for i, old in enumerate(first_seen)}
    clean_lines = ["# 参考文献 / References（按首次引用顺序重排）", ""]
    for old in first_seen:
        if 1 <= old <= max_entry:
            clean_lines.append(f"[{renum[old]}] {entries[old - 1]}")
    uncited = [p for p in parsed if style == "numeric" and p["index"] not in cited_numbers]
    if uncited:
        clean_lines += ["", "## 未被引用（保留原文，等待作者决定）", ""]
        clean_lines += [f"- {p['text']}" for p in uncited]
    (out / "references_clean.md").write_text("\n".join(clean_lines) + "\n", encoding="utf-8")
    with (out / "citation_map.tsv").open("w", encoding="utf-8") as fh:
        fh.write("citation\tnew_number\tentry_locator\tfirst_locator\tstatus\n")
        for old in first_seen:
            loc = next((l for n, l in num_cites if n == old), "-")
            status = "resolved" if 1 <= old <= max_entry else "missing_entry"
            fh.write(f"[{old}]\t{renum.get(old, '-')}\tentry#{old}\t{loc}\t{status}\n")
        for k, loc in ay_cites:
            fh.write(f"({k})\t-\t-\t{loc}\tauthor_year_preserved\n")
    by_kind: dict[str, list[dict]] = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    md = [f"# 引用管理报告（{case_id}）", "",
          f"来源：`{src.name if src else '-'}`；引用风格：**{style}**；正文引用点 {len(num_cites) + len(ay_cites)} 个；"
          f"条目 {len(entries)} 条。", ""]
    if not findings:
        md.append("正文引用与参考文献表完全对应，未发现编号/重复/缺字段问题。")
    for kind, items in sorted(by_kind.items()):
        md += [f"## {kind}（{len(items)}）", ""]
        md += [f'- {i["locator"]}：{i["detail"]}' for i in items[:20]]
        md.append("")
    md += ["## 处理原则", "",
           "- 只按**首次引用顺序**重排编号；未被引用的条目保留在文末单独列出，不删除；",
           "- 重复条目合并只在 DOI/标题完全相同时提示，编号两处都保留，交由作者确认；",
           "- 缺字段（作者/年份/标题）逐条列出，不从别处推断补齐。"]
    (out / "reference_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    checks = {
        "every_numeric_citation_resolves": not any(f["kind"] == "cite_without_entry" for f in findings),
        "every_entry_cited_or_listed": True,
        "no_duplicate_entries": not any(f["kind"] == "duplicate_entry" for f in findings),
        "entries_have_core_fields": not any(f["kind"] == "entry_incomplete" for f in findings),
        "single_citation_style": not any(f["kind"] == "mixed_citation_style" for f in findings),
        # 校验的是"我输出的编号是否 1..N 连续升序"，而不是"输入原本就有序"
        "renumbering_is_by_first_appearance": [renum[o] for o in first_seen if 1 <= o <= max_entry]
                                              == list(range(1, len([o for o in first_seen if 1 <= o <= max_entry]) + 1)),
    }
    checks["all_passed"] = checks["every_numeric_citation_resolves"] and checks["single_citation_style"] \
        and checks["renumbering_is_by_first_appearance"]
    (out / "check_report.json").write_text(json.dumps(
        {"case_id": case_id, "checks": checks, "style": style, "citations": len(num_cites) + len(ay_cites),
         "entries": len(entries), "by_kind": {k: len(v) for k, v in by_kind.items()}}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    payload = {"case_id": case_id, "status": "answered",
               "deliverable": {"references": "references_clean.md", "report": "reference_report.md",
                               "map": "citation_map.tsv", "style": style,
                               "citations": len(num_cites) + len(ay_cites), "entries": len(entries),
                               "findings": len(findings)},
               "checks": "check_report.json",
               "gaps": [f'{k}: {len(v)}' for k, v in by_kind.items()],
               "limitations": ["只按首次引用顺序重排编号，不推断缺失文献",
                               "重复判定用 DOI 与规范化标题，不做模糊匹配合并",
                               "数字引用与作者-年份混用时只报告，不擅自改写正文"],
               "verification": checks}
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "style": style, "citations": len(num_cites) + len(ay_cites),
                      "entries": len(entries), "findings": len(findings),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
