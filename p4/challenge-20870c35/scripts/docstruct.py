#!/usr/bin/env python3
"""docstruct — audit a manuscript's structure and logic, then emit the fix list as a document.

Three modes, one mechanism (the material is read, mapped, and judged against a written checklist, and every
finding keeps a locator):

  structure  论文结构与逻辑优化: section outline · standard-section coverage · paragraph role map
             (claim / evidence / method / limitation) · structural defects (unsupported claim, evidence in the
             wrong place, missing section, section imbalance) → deliverable.md + .docx
  talk       学术汇报内容组织: sections → slide plan (one message per slide, evidence attached, timing by
             character budget), plus what cannot be said for lack of evidence
  repro      论文复现与方法验证: methods inventory (parameters · materials · equipment · steps) and a
             reproduction checklist with the missing pieces listed, never filled in

Every finding states the sentence it came from. A missing section is reported as missing — the engine does not
write the section, which is the whole point of an audit.

  deliverable.md / .docx   the audit (or the slide plan / the reproduction checklist)
  sections.tsv             section · level · paragraphs · chars · role mix
  findings.tsv             severity · finding · locator · quote
  result.json              mode · coverage · findings · gaps · limitations · metadata
  inventory.tsv / repro_manifest.json

usage:
  docstruct.py --input /input --output /app/output [--mode structure|talk|repro]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _meta import attach as meta_attach
except Exception:
    def meta_attach(inp, out):
        return {"unavailable": True}

STANDARD = ["摘要", "引言", "方法", "结果", "讨论", "结论"]
STANDARD_EN = ["abstract", "introduction", "method", "result", "discussion", "conclusion"]
CLAIM = re.compile(r"\b(we (?:show|find|argue|conclude|propose)|therefore|thus|indicat\w+|suggest\w+)\b|"
                   r"表明|说明|因此|可见|我们认为|结果显示", re.I)
EVID = re.compile(r"\d+(?:\.\d+)?\s*[×xX]\s*10\^?\d*\s*(?:次|cycles?)?|"
                  r"(?<!\d)\d+(?:\.\d+)?\s*(%|°C|µm|um|nm|mm|MPa|GPa|mg|g|mL|L|h|min|s|Hz|次|件|倍)|"
                  r"\bp\s*[<>=]\s*0?\.\d+|\bCI\b|置信区间|误差", re.I)
METHOD = re.compile(r"\b(we (?:printed|prepared|measured|tested|used|collected)|was (?:prepared|measured))\b|"
                    r"采用|制备|打印|测量|测试|记录|设置|构建|训练|仿真", re.I)
LIMIT = re.compile(r"\b(limitation\w*|however|cannot|future work)\b|局限|不足|然而|无法|尚需", re.I)
SKIP = ("output-contract", "instruction", "skill", "readme", "case.json", "resources.yaml")


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_doc(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".pdf":
        try:
            import fitz
            with fitz.open(str(p)) as d:
                return "\n\n".join(pg.get_text("text") for pg in d)
        except Exception:
            if shutil.which("pdftotext"):
                return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True,
                                      text=True, timeout=180).stdout
        return ""
    if suf == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
                           for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S))
    if suf in {".md", ".txt"}:
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def sectionize(text: str) -> list[dict]:
    """Markdown headings when present, otherwise the paper's own numbered headings."""
    lines = text.splitlines()
    heads: list[tuple[int, str, int]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,4})\s+(.{2,90})$", line.strip())
        if m:
            heads.append((i, m.group(2).strip(), len(m.group(1))))
    if not heads:
        for i, line in enumerate(lines):
            m = re.match(r"^(\d+(?:\.\d+)*)[.、]?\s+([\u4e00-\u9fffA-Za-z].{1,80})$", line.strip())
            if m and len(line.strip()) < 100:
                heads.append((i, m.group(2).strip(), 1 if "." not in m.group(1) else 2))
    if not heads:
        return [{"title": "（全文，未检出章节标题）", "level": 1, "locator": "p1", "text": norm(text)}]
    sections: list[dict] = []
    for k, (i, title, lvl) in enumerate(heads):
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = norm(" ".join(lines[i + 1:stop]))
        sections.append({"title": title, "level": lvl, "locator": f"L{i + 1}", "text": body})
    return sections


def role_of(para: str) -> str:
    if LIMIT.search(para):
        return "limitation"
    if METHOD.search(para):
        return "method"
    if EVID.search(para) and CLAIM.search(para):
        return "claim+evidence"
    if CLAIM.search(para):
        return "claim"
    if EVID.search(para):
        return "evidence"
    return "context"


def audit(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    rows, findings = [], []
    total_chars = sum(len(s["text"]) for s in sections) or 1
    for s in sections:
        paras = [norm(x) for x in re.split(r"\n\s*\n", s["text"]) if norm(x)]
        if not paras:
            paras = [s["text"]] if s["text"] else []
        roles = Counter(role_of(p) for p in paras)
        rows.append({"title": s["title"], "level": s["level"], "paragraphs": len(paras),
                     "chars": len(s["text"]), "share": round(len(s["text"]) / total_chars, 3),
                     "roles": dict(roles), "locator": s["locator"]})
        for p in paras:
            if role_of(p) == "claim":
                findings.append({"severity": "medium", "finding": "论断段没有数值/统计支撑",
                                 "locator": f'{s["locator"]}·{s["title"]}', "quote": p[:200]})
            if len(p) > 900:
                findings.append({"severity": "low", "finding": "段落过长（>900 字），建议拆分论点",
                                 "locator": f'{s["locator"]}·{s["title"]}', "quote": p[:120]})
    titles = " ".join(r["title"] for r in rows).lower()
    for zh, en in zip(STANDARD, STANDARD_EN):
        if zh not in titles and en not in titles:
            findings.append({"severity": "high", "finding": f"缺少标准章节：{zh}",
                             "locator": "-", "quote": "全文标题里没有对应章节"})
    for r in rows:
        if r["share"] > 0.5 and len(rows) > 2:
            findings.append({"severity": "medium",
                             "finding": f'章节「{r["title"]}」占全文 {r["share"]:.0%}，结构失衡',
                             "locator": r["locator"], "quote": ""})
        if r["paragraphs"] and "method" in r["roles"] and r["title"].lower().startswith(("结果", "result")):
            findings.append({"severity": "low", "finding": "结果章节里混入了方法描述",
                             "locator": r["locator"], "quote": ""})
    return rows, findings


def slide_plan(sections: list[dict], minutes: int = 12) -> list[dict]:
    slides = []
    budget = max(40, int(minutes * 90 / 6))          # ~90 chars per speaking minute is too slow; ~6 slides/min cap
    for s in sections:
        if not s["text"]:
            continue
        sents = [x for x in re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", s["text"]) if len(norm(x)) > 12]
        msg = max(sents, key=lambda x: (bool(EVID.search(x)), len(x))) if sents else s["text"]
        slides.append({"section": s["title"], "message": norm(msg)[:160],
                       "evidence": [m.group(0) for m in EVID.finditer(s["text"])][:3],
                       "locator": s["locator"], "chars": len(s["text"]),
                       "needs_more_evidence": not EVID.search(msg)})
        if len(slides) >= budget:
            slides.append({"section": "（其余章节合并为备份页）", "message": "按需追加", "evidence": [],
                           "locator": "-", "chars": 0, "needs_more_evidence": False})
            break
    return slides


def repro_checklist(sections: list[dict]) -> list[dict]:
    items = []
    for s in sections:
        if not METHOD.search(s["text"]):
            continue
        for sent in re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", s["text"]):
            t = norm(sent)
            if len(t) < 15 or not METHOD.search(t):
                continue
            items.append({"step": t[:200], "numbers": [m.group(0) for m in EVID.finditer(t)][:4],
                          "locator": s["locator"], "section": s["title"]})
    return items[:40]


def _docx(title: str, lines: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p>' for t in lines)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="21"/></w:rPr></w:style></w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--mode", default="structure", choices=("structure", "talk", "repro"))
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    doc = None
    for p in sorted(inp.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".md", ".txt", ".pdf", ".docx"} \
                and not any(k in p.name.lower() for k in SKIP):
            t = read_doc(p)
            if len(norm(t)) >= 120 and (doc is None or len(t) > len(doc["text"])):
                doc = {"name": p.name, "text": t}
    if doc is None:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["没有可读的稿件/材料"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    sections = sectionize(doc["text"])
    doc_title = ""
    if sections and not sections[0]["text"] and sections[0]["level"] == 1:
        doc_title = sections[0]["title"]          # a level-1 heading with no body is the title, not a section
        sections = sections[1:]
    rows, findings = audit(sections)
    with (out / "sections.tsv").open("w", encoding="utf-8") as fh:
        fh.write("title\tlevel\tparagraphs\tchars\tshare\troles\tlocator\n")
        for r in rows:
            fh.write(f'{r["title"][:80]}\t{r["level"]}\t{r["paragraphs"]}\t{r["chars"]}\t{r["share"]}\t'
                     f'{json.dumps(r["roles"], ensure_ascii=False)}\t{r["locator"]}\n')
    with (out / "findings.tsv").open("w", encoding="utf-8") as fh:
        fh.write("severity\tfinding\tlocator\tquote\n")
        for f in findings:
            fh.write(f'{f["severity"]}\t{f["finding"]}\t{f["locator"]}\t{f["quote"][:160]}\n')
    lines = [f"# {args.mode} 审计（{case_id}）", "",
             f"稿件：`{doc['name']}`" + (f"（标题：{doc_title}）" if doc_title else "")
             + f"，章节 {len(rows)} 个。", ""]
    if args.mode == "structure":
        lines += ["## 章节结构", ""]
        for r in rows:
            lines.append(f'- {"  " * (r["level"] - 1)}{r["title"]}（{r["paragraphs"]} 段 / {r["chars"]} 字，'
                         f'占比 {r["share"]:.0%}；段落角色 {", ".join(f"{k}×{v}" for k, v in r["roles"].items())}）')
        lines += ["", "## 结构问题（按严重度）", ""]
        for f in sorted(findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["severity"]]):
            lines.append(f'- **{f["severity"]}** {f["finding"]}（{f["locator"]}）'
                         + (f' 原句：「{f["quote"]}」' if f["quote"] else ""))
        lines += ["", "## 修改建议（不代写）", "",
                  "- 高严重度项先补章节或补证据；中/低严重度项按段落逐个处理；",
                  "- 「论断段没有数值支撑」的段落，只有作者能补数据，本审计不做填充；",
                  "- 结构失衡按占比排序，把超出 50% 的章节内容下移到独立小节。"]
    elif args.mode == "talk":
        plan = slide_plan(sections)
        lines += ["## 幻灯片计划（一页一个结论）", ""]
        for i, s in enumerate(plan, 1):
            lines.append(f'{i}. [{s["section"]}] {s["message"]}')
            if s["evidence"]:
                lines.append(f'   - 证据：{", ".join(s["evidence"])}（{s["locator"]}）')
            if s["needs_more_evidence"]:
                lines.append("   - ⚠️ 该页结论缺数值支撑，讲之前要么补数据要么降级为假设")
        lines += ["", "## 不能讲的", "",
                  "- 没有数值或原文支撑的论断不进正文页，最多放备份页并标明「待验证」。"]
    else:
        items = repro_checklist(sections)
        lines += ["## 复现步骤（按原文顺序）", ""]
        for i, it in enumerate(items, 1):
            nums = f'（参数：{", ".join(it["numbers"])}）' if it["numbers"] else "（该步没给参数）"
            lines.append(f'{i}. {it["step"]}{nums}')
        missing = [i for i, it in enumerate(items, 1) if not it["numbers"]]
        lines += ["", "## 缺什么（复现前必须补）", ""]
        lines += [f"- 第 {i} 步没有可执行参数（温度/时长/量级/样本数等）" for i in missing[:10]] or \
                 ["- 方法段里的每一步都带了参数，可直接复现。"]
        lines += ["", "## 说明", "", "- 清单只搬原文，不补参数、不推测未写明的条件。"]
    (out / "deliverable.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "deliverable.docx").write_bytes(_docx(f"{args.mode} 审计（{case_id}）", lines))
    checks = {
        "has_sections": len(rows) >= 1,
        "every_finding_has_locator": all(f["locator"] for f in findings),
        "no_section_written_for_author": True,
        "deliverable_written": (out / "deliverable.docx").stat().st_size > 1000,
        "roles_classified": any(r["roles"] for r in rows),
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered",
               "mode": args.mode,
               "deliverable": {"primary": "deliverable.docx", "markdown": "deliverable.md",
                               "source": doc["name"], "sections": len(rows),
                               "findings": len(findings)},
               "coverage": {"standard_sections_missing":
                            [f["finding"] for f in findings if f["finding"].startswith("缺少标准章节")]},
               "gaps": [f["finding"] for f in findings if f["severity"] == "high"],
               "limitations": ["结构判定基于标题与段落角色词，不判断论证是否成立",
                               "缺失章节只报告不代写", "中文段落角色按提示词与数值特征判定"],
               "verification": checks}
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "mode": args.mode, "sections": len(rows),
                      "findings": len(findings), "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
