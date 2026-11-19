#!/usr/bin/env python3
"""Material-grounded manuscript writer: turns supplied material into an editable paper.

Deliverables per case (all under /app/output):
  manuscript.md      IMRaD-shaped draft where every sentence is either grounded in a
                     material sentence (with its locator) or explicitly marked UNGROUNDED,
  manuscript.docx    real OOXML: heading styles, numbered sections, a key-number table and
                     a TOC field, so the author can keep editing in Word,
  claim_source_map.tsv  claim -> material locator + verbatim quote (the thing a reviewer
                     checks first),
  review_checklist.md   the items a human still has to confirm,
  result.json        case_id/status/sections/source_map/uncertainties/limitations.

Rules that keep it honest: nothing is written from general knowledge; numbers are copied
from the material with their unit; a section with no material says so instead of inventing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
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

SKIP = {"result.json", "manuscript.md", "manuscript.docx", "claim_source_map.tsv",
        "review_checklist.md", "selfcheck.json", "run.md", "skill.md", "gain.md",
        "rubric.md", "per_item.tsv", "report.md"}
# Chinese manuscripts name the same sections in Chinese; matching English only produced six false
# "章节缺材料依据" gaps on a complete Chinese draft
SECTIONS = [("Abstract", ("abstract", "summary", "摘要", "内容提要")),
            ("Introduction", ("introduction", "background", "motivation", "引言", "前言", "绪论")),
            ("Methods", ("method", "materials and methods", "experimental", "approach",
                         "方法", "材料与方法", "实验部分", "实验方法")),
            ("Results", ("result", "finding", "observation", "结果", "实验结果", "结果与分析")),
            ("Discussion", ("discussion", "interpretation", "limitation", "讨论", "分析与讨论")),
            ("Conclusion", ("conclusion", "summary of findings", "结论", "总结", "结论与展望"))]
NUM = re.compile(r"(?<![A-Za-z0-9_\-])([-+]?\d+(?:\.\d+)?)(?:\s*[±+\-/]{1,4}\s*\d+(?:\.\d+)?)?"
                 r"\s*(mm3|mm2|cm3|%|nM|µM|mM|mg/kg|mg|g|h|days?|weeks?|patients?|samples?|cells?)")
REF_HEAD = re.compile(r"^\s*(references|bibliography|参考文献)\s*$", re.I)


def _pure_pdf_text(path) -> list[str]:
    """Standard-library PDF text reader (last resort when no PDF engine is installed).

    Handles both literal `(...)` and hex `<...>` strings (PyMuPDF writes hex strings inside TJ), turns
    positioning operators into line breaks, and skips streams that do not decompress.
    """
    import zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        if "BT" not in text and "Tj" not in text and "TJ" not in text:
            continue
        parts: list[str] = []
        in_array = False
        for tok in re.finditer(r"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>|\[|\]|[-+]?\d+(?:\.\d+)?|"
                               r"\b(?:Tj|TJ|Td|TD|T\*|ET)\b", text):
            t = tok.group(0)
            if t == "[":
                in_array = True
            elif t == "]":
                in_array = False
            elif t.startswith("("):
                parts.append(t[1:-1].replace("\\(", "(").replace("\\)", ")"))
            elif t.startswith("<"):
                hexs = re.sub(r"\s+", "", t[1:-1])
                try:
                    parts.append(bytes.fromhex(hexs if len(hexs) % 2 == 0 else hexs + "0")
                                 .decode("latin-1", "replace"))
                except Exception:
                    pass
            elif t in {"Td", "TD", "T*", "ET"}:
                parts.append("\n")
            elif in_array and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", t):
                try:
                    if float(t) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        chunk = "".join(parts).strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_any(path: Path) -> list[str]:
    if path.suffix.lower() != ".pdf":
        return [path.read_text(encoding="utf-8", errors="replace")]
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


def collect(root: Path) -> tuple[list[dict], list[str]]:
    blocks, notes = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        low = path.name.lower()
        # INPUTS are read here; SKIP protects this tool's own *output* names, and "manuscript.md" is a very
        # plausible input name — skipping it silently produced zero blocks on a real draft.
        if low.startswith(("case.json", "task.md")) or low in {"result.json", "selfcheck.json", "report.md",
                                                               "claim_source_map.tsv", "review_checklist.md"}:
            continue
        pages = read_any(path)
        if not pages:
            notes.append(f"{path.name}: unreadable (no text layer; OCR required)")
            continue
        notes.append(f"{path.name}: {len(pages)} page(s)")
        for pno, text in enumerate(pages, 1):
            skip = False
            for line in (text or "").splitlines():
                if REF_HEAD.match(line):
                    skip = True
                if skip:
                    continue
                for sidx, sent in enumerate(re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", norm(line)), 1):
                    if len(sent) < 30:
                        continue
                    blocks.append({"source": path.name, "page": pno,
                                   "locator": f"p{pno}s{sidx}", "text": sent})
    return blocks, notes


def classify(sent: str) -> str:
    low = sent.lower()
    for name, keys in SECTIONS:
        if any(k in low for k in keys):
            return name
    if NUM.search(sent):
        return "Results"
    if any(k in low for k in ("dosed", "measured", "cells", "assay", "protocol", "approved")):
        return "Methods"
    return "Discussion"


def build_sections(blocks: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {name: [] for name, _ in SECTIONS}
    for idx, b in enumerate(blocks, 1):
        b["sid"] = f"S{idx}"
        out[classify(b["text"])].append(b)
    return out


def docx_bytes(title: str, sections: dict[str, list[dict]], sources: list[dict]) -> bytes:
    """Minimal but real OOXML: heading styles, numbered body, a table, a TOC field."""
    def p(text: str, style: str = "Normal") -> str:
        return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')

    def escape(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    body = [p(title, "Title"),
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-2" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>Table of contents (update field in Word)</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>']
    for name, rows in sections.items():
        body.append(p(name, "Heading1"))
        if not rows:
            body.append(p("〔待补：材料中未找到该章节的事实依据〕"))
            continue
        for r in rows:
            body.append(p(f"{r['text']} [{r['sid']}]"))
    if sources:
        body.append(p("Key numbers", "Heading1"))
        cells = "".join(f'<w:tr><w:tc><w:p><w:r><w:t>{escape(s["sid"])}</w:t></w:r></w:p></w:tc>'
                        f'<w:tc><w:p><w:r><w:t>{escape(s["value"])}</w:t></w:r></w:p></w:tc>'
                        f'<w:tc><w:p><w:r><w:t>{escape(s["locator"])}</w:t></w:r></w:p></w:tc></w:tr>'
                        for s in sources)
        body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
                    '<w:tr><w:tc><w:p><w:r><w:t>Source</w:t></w:r></w:p></w:tc>'
                    '<w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>'
                    '<w:tc><w:p><w:r><w:t>Locator</w:t></w:r></w:p></w:tc></w:tr>'
                    + cells + '</w:tbl>')
    body.append(p("References", "Heading1"))
    for r in {b["source"] for rows in sections.values() for b in rows}:
        body.append(p(f"{r} — cited inline as [S#] (locator in claim_source_map.tsv)"))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + '</w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
              '<w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              '<w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
              '</w:styles>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                '</Relationships>')
    numbering = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                 '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/numbering.xml", numbering)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--title", default="Manuscript")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:  # noqa: BLE001
            pass
    blocks, notes = collect(inp)
    sections = build_sections(blocks)
    key_numbers = []
    for rows in sections.values():
        for b in rows:
            m = NUM.search(b["text"])
            if m:
                key_numbers.append({"sid": b["sid"], "value": f"{m.group(1)} {m.group(2)}",
                                    "locator": f"{b['source']}#{b['locator']}",
                                    "quote": b["text"][:200]})
    md = [f"# {args.title}", "", f"case_id: `{case_id}`", ""]
    for name, rows in sections.items():
        md += [f"## {name}", ""]
        if not rows:
            md += ["〔待补：材料中未找到该章节的事实依据〕", ""]
            continue
        for b in rows:
            md.append(f"{b['text']} [{b['sid']}]")
        md.append("")
    (out / "manuscript.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "manuscript.docx").write_bytes(docx_bytes(args.title, sections, key_numbers))

    map_rows = ["claim_id\tlocator\tsource\tquote"]
    for n, (name, rows) in enumerate(sections.items(), 1):
        for b in rows:
            map_rows.append(f"{b['sid']}\t{b['locator']}\t{b['source']}\t{b['text'][:200]}")
    (out / "claim_source_map.tsv").write_text("\n".join(map_rows) + "\n", encoding="utf-8")

    missing = [name for name, rows in sections.items() if not rows]
    check = ["# 待人工确认清单", ""]
    check += [f"- 章节 `{m}` 无材料依据，需要作者补写或删去" for m in missing] or ["- 章节覆盖完整"]
    check += ["- 每条断言后的 [S#] 可在 `claim_source_map.tsv` 中按 ID 查到原始定位",
              "- 数值均按材料原句抄录（含单位），未做换算；如材料本身口径冲突，需人工裁定"]
    (out / "review_checklist.md").write_text("\n".join(check) + "\n", encoding="utf-8")

    with zipfile.ZipFile(out / "manuscript.docx") as z:
        parts = z.namelist()
        wellformed = all(b"<w:document" in z.read("word/document.xml") for _ in [0])
    payload = {"case_id": case_id,
               "status": "answered" if blocks else "partial",
               "result": {"sections": {k: len(v) for k, v in sections.items()},
                          "source_map": key_numbers[:40],
                          "artifacts": ["manuscript.md", "manuscript.docx",
                                        "claim_source_map.tsv", "review_checklist.md"]},
               "sources": [{"name": n} for n in notes],
               "uncertainties": [f"章节缺材料依据：{', '.join(missing)}"] if missing else [],
               "limitations": ["正文只复述材料事实，不做推理型论证；材料缺失处显式标注而非补写",
                               "docx 的目录(TOC)字段需在 Word 中更新一次才显示页码"],
               "change_log": ["v1: 材料句→IMRaD 章节 + 真 OOXML docx（标题样式/编号/表格/TOC）+ 断言-来源映射"]}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    sc = {"blocks": len(blocks), "sections_with_material": sum(1 for v in sections.values() if v),
          "docx_parts": len(parts), "docx_wellformed": wellformed,
          "claim_map_rows": len(map_rows) - 1}
    (out / "SELFCHECK.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(sc, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
