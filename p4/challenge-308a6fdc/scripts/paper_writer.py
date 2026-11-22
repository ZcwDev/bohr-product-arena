#!/usr/bin/env python3
"""Material-grounded manuscript writer: turns supplied research material into an
editable academic paper.

Deliverables per case (all under output directory):
  manuscript.md         IMRaD-shaped draft where every sentence is grounded in
                        material (with locator tag [S#]) or marked 〔待补〕,
  manuscript.docx       real OOXML: heading styles, numbered sections, a key-number
                        table and a TOC field, so the author can keep editing in Word,
  claim_source_map.tsv  claim_id -> material locator + verbatim quote,
  review_checklist.md   items a human still has to confirm,
  result.json           case_id + all output_contract fields.

Output contract (result.json):
  case_id          : echo of input case_id
  deliverable      : description of editable manuscript/structured output
  preserved_facts  : numbers, terms, conditions preserved verbatim with source locators
  evidence_map     : claim -> source location mapping
  change_log       : actions taken, reasons, human-confirmation items, unresolved issues
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


# ---------------------------------------------------------------------------
# Python interpreter shim: try science-python with PyMuPDF if available
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SKIP_NAMES = {
    "result.json", "manuscript.md", "manuscript.docx", "claim_source_map.tsv",
    "review_checklist.md", "selfcheck.json", "run.md", "skill.md", "gain.md",
    "rubric.md", "per_item.tsv", "report.md", "case.json", "task.md",
    "output-contract.md", "example-input.json",
}
SKIP_PREFIXES = ("case.json", "task.md", "output-contract", "example-input")

SECTIONS = [
    ("Abstract", ("abstract", "summary", "摘 要", "摘要")),
    ("Introduction", ("introduction", "background", "motivation", "引言", "前言")),
    ("Methods", ("method", "materials and methods", "experimental", "approach",
                 "材料与方法", "方法", "实验")),
    ("Results", ("result", "finding", "observation", "结果", "发现")),
    ("Discussion", ("discussion", "interpretation", "limitation", "讨论")),
    ("Conclusion", ("conclusion", "summary of findings", "结论", "总结")),
]

# Regex for number+unit extraction (preserves verbatim from material)
NUM = re.compile(
    r"(?<![A-Za-z0-9_\-])([-+]?\d+(?:\.\d+)?)(?:\s*[±+\-/]{1,4}\s*\d+(?:\.\d+)?)?"
    r"\s*(mm3|mm2|cm3|cm2|%|nM|µM|mM|mg/kg|mg/ml|mg|g|kg|µg|ng|h|min|s|days?|weeks?|"
    r"months?|years?|patients?|samples?|cells?|mL|µL|L|mL/kg|IU|U/L|mmHg|°C|bp|kb|kDa|"
    r"nmol|pmol|nm|μm|µm)"
)

REF_HEAD = re.compile(r"^\s*(references|bibliography|参考文献)\s*$", re.I)


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------
def _pure_pdf_text(path) -> list[str]:
    """Last-resort text extraction with the standard library only.
    Walks PDF content streams, inflates with zlib, collects text-showing operators.
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


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_any(path: Path) -> list[str]:
    """Read text from any supported file type. Returns list of page-texts."""
    if path.suffix.lower() != ".pdf":
        return [path.read_text(encoding="utf-8", errors="replace")]
    # Try PyMuPDF
    try:
        import fitz
    except Exception:
        fitz = None
    if fitz is not None:
        try:
            return [pg.get_text("text") for pg in fitz.open(str(path))]
        except Exception:
            pass
    # Try pure-python PDF extraction
    pages = _pure_pdf_text(path)
    if pages:
        return pages
    # Try pdftotext CLI
    if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
        try:
            return subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True, text=True, timeout=120
            ).stdout.split("\f")
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Material collection
# ---------------------------------------------------------------------------
def _should_skip(path: Path) -> bool:
    low = path.name.lower()
    if low in SKIP_NAMES:
        return True
    for pfx in SKIP_PREFIXES:
        if low.startswith(pfx):
            return True
    return False


def collect(root: Path) -> tuple[list[dict], list[str]]:
    """Collect all material sentences from the input directory."""
    blocks, notes = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        if _should_skip(path):
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
                for sidx, sent in enumerate(re.split(r"(?<=[.!?])\s+", norm(line)), 1):
                    if len(sent) < 20:
                        continue
                    blocks.append({
                        "source": path.name,
                        "page": pno,
                        "locator": f"p{pno}s{sidx}",
                        "text": sent,
                    })
    return blocks, notes


# ---------------------------------------------------------------------------
# Section classification
# ---------------------------------------------------------------------------
def classify(sent: str) -> str:
    low = sent.lower()
    for name, keys in SECTIONS:
        if any(k in low for k in keys):
            return name
    if NUM.search(sent):
        return "Results"
    if any(k in low for k in ("dosed", "measured", "cells", "assay", "protocol",
                               "approved", "cultured", "stained", "analyzed")):
        return "Methods"
    return "Discussion"


def build_sections(blocks: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {name: [] for name, _ in SECTIONS}
    for idx, b in enumerate(blocks, 1):
        b["sid"] = f"S{idx}"
        out[classify(b["text"])].append(b)
    return out


# ---------------------------------------------------------------------------
# DOCX generation (real OOXML, pure standard library)
# ---------------------------------------------------------------------------
def docx_bytes(title: str, sections: dict[str, list[dict]], sources: list[dict]) -> bytes:
    """Minimal but real OOXML: heading styles, numbered body, a table, a TOC field."""
    def escape(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def p(text: str, style: str = "Normal") -> str:
        return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')

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
        cells = "".join(
            f'<w:tr><w:tc><w:p><w:r><w:t>{escape(s["sid"])}</w:t></w:r></w:p></w:tc>'
            f'<w:tc><w:p><w:r><w:t>{escape(s["value"])}</w:t></w:r></w:p></w:tc>'
            f'<w:tc><w:p><w:r><w:t>{escape(s["locator"])}</w:t></w:r></w:p></w:tc></w:tr>'
            for s in sources
        )
        body.append(
            '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
            '<w:tr><w:tc><w:p><w:r><w:t>Source</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Locator</w:t></w:r></w:p></w:tc></w:tr>'
            + cells + '</w:tbl>'
        )
    body.append(p("References", "Heading1"))
    for r in {b["source"] for rows in sections.values() for b in rows}:
        body.append(p(f"{r} — cited inline as [S#] (locator in claim_source_map.tsv)"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(body) + '</w:body></w:document>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:pPr><w:outlineLvl w:val="1"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
        '</w:styles>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" '
        'Target="numbering.xml"/>'
        '</Relationships>'
    )
    numbering = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
        '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/numbering.xml", numbering)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Case ID resolution (flexible: case.json, or any JSON with case_id field)
# ---------------------------------------------------------------------------
def resolve_case_id(inp: Path) -> str:
    """Find case_id from input directory. Tries case.json first, then any JSON."""
    cj = inp / "case.json"
    if cj.is_file():
        try:
            return str(json.loads(cj.read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    # Fallback: any JSON file with a case_id field
    for f in sorted(inp.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            cid = data.get("case_id")
            if cid:
                return str(cid)
        except Exception:
            continue
    return "unreported"


# ---------------------------------------------------------------------------
# Task instruction extraction
# ---------------------------------------------------------------------------
def read_task_instruction(inp: Path) -> str:
    """Read task instruction from case.json or any JSON file."""
    for fname in ("case.json",):
        f = inp / fname
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return str(data.get("task_instruction", ""))
            except Exception:
                pass
    for f in sorted(inp.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ti = data.get("task_instruction", "")
            if ti:
                return str(ti)
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Material-grounded manuscript writer")
    ap.add_argument("--input", default="/input", help="Input directory with case.json and materials")
    ap.add_argument("--output", default="/app/output", help="Output directory for deliverables")
    ap.add_argument("--title", default=None, help="Manuscript title (auto-detected if omitted)")
    args = ap.parse_args()

    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Resolve case_id
    case_id = resolve_case_id(inp)
    task_instruction = read_task_instruction(inp)

    # 2. Auto-title from instruction or case_id
    title = args.title
    if not title:
        if task_instruction and len(task_instruction) < 80:
            title = task_instruction
        else:
            title = f"Manuscript ({case_id})"

    # 3. Collect material blocks
    blocks, notes = collect(inp)

    # 4. Build IMRaD sections
    sections = build_sections(blocks)

    # 5. Extract key numbers (preserved facts)
    key_numbers = []
    for rows in sections.values():
        for b in rows:
            for m in NUM.finditer(b["text"]):
                key_numbers.append({
                    "sid": b["sid"],
                    "value": m.group(0).strip(),
                    "locator": f"{b['source']}#{b['locator']}",
                    "quote": b["text"][:200],
                })

    # 6. Write manuscript.md
    md = [f"# {title}", "", f"case_id: `{case_id}`", ""]
    for name, rows in sections.items():
        md += [f"## {name}", ""]
        if not rows:
            md += ["〔待补：材料中未找到该章节的事实依据〕", ""]
            continue
        for b in rows:
            md.append(f"{b['text']} [{b['sid']}]")
        md.append("")
    (out / "manuscript.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # 7. Write manuscript.docx
    (out / "manuscript.docx").write_bytes(docx_bytes(title, sections, key_numbers))

    # 8. Write claim_source_map.tsv
    map_rows = ["claim_id\tlocator\tsource\tquote"]
    for name, rows in sections.items():
        for b in rows:
            map_rows.append(f"{b['sid']}\t{b['locator']}\t{b['source']}\t{b['text'][:200]}")
    (out / "claim_source_map.tsv").write_text("\n".join(map_rows) + "\n", encoding="utf-8")

    # 9. Write review_checklist.md
    missing = [name for name, rows in sections.items() if not rows]
    check = ["# 待人工确认清单", ""]
    if missing:
        check += [f"- 章节 `{m}` 无材料依据，需要作者补写或删去" for m in missing]
    else:
        check += ["- 章节覆盖完整"]
    check += [
        "- 每条断言后的 [S#] 可在 `claim_source_map.tsv` 中按 ID 查到原始定位",
        "- 数值均按材料原句抄录（含单位），未做换算；如材料本身口径冲突，需人工裁定",
    ]
    (out / "review_checklist.md").write_text("\n".join(check) + "\n", encoding="utf-8")

    # 10. Build evidence_map (structured form of claim_source_map)
    evidence_map = []
    for name, rows in sections.items():
        for b in rows:
            evidence_map.append({
                "claim_id": b["sid"],
                "section": name,
                "locator": f"{b['source']}#{b['locator']}",
                "source_file": b["source"],
                "page": b["page"],
                "verbatim_quote": b["text"][:300],
            })

    # 11. Build preserved_facts
    preserved_facts = []
    seen = set()
    for kn in key_numbers:
        key = (kn["sid"], kn["value"])
        if key in seen:
            continue
        seen.add(key)
        preserved_facts.append({
            "fact": kn["value"],
            "claim_id": kn["sid"],
            "locator": kn["locator"],
            "method": "verbatim_copy_with_unit",
        })
    # Also note that all text is copied verbatim
    preserved_facts_summary = (
        f"共提取 {len(key_numbers)} 个数值事实（含单位），全部从材料原句抄录，未做换算或改写。"
        f"正文每条句子直接来自材料原文，以 [S#] 标记来源定位。"
    )

    # 12. Build change_log
    change_log = []
    change_log.append({
        "action": "material_collection",
        "description": f"从 {len(notes)} 个材料文件中提取了 {len(blocks)} 条有效句子",
    })
    change_log.append({
        "action": "section_classification",
        "description": "将材料句按关键词归入 IMRaD 六章节",
    })
    change_log.append({
        "action": "number_preservation",
        "description": f"提取 {len(key_numbers)} 个数值（含单位），原样抄录，未做换算",
    })
    if missing:
        change_log.append({
            "action": "missing_sections_marked",
            "description": f"以下章节无材料依据，标记为〔待补〕：{', '.join(missing)}",
            "human_confirmation_required": True,
        })
    change_log.append({
        "action": "docx_generation",
        "description": "生成真 OOXML .docx（Title/Heading1 样式、编号定义、表格、TOC 域）",
    })
    change_log.append({
        "action": "evidence_mapping",
        "description": f"生成 {len(evidence_map)} 条断言→来源映射，可逐条回溯核验",
    })
    if missing:
        change_log.append({
            "action": "unresolved",
            "description": f"章节缺材料依据：{', '.join(missing)}，需作者补写或提供额外材料",
            "unresolved": True,
        })

    # 13. Build deliverable description
    deliverable = {
        "format": "Markdown (.md) + OOXML (.docx)",
        "files": [
            "manuscript.md",
            "manuscript.docx",
            "claim_source_map.tsv",
            "review_checklist.md",
        ],
        "description": (
            f"IMRaD 结构学术稿件，含 {sum(len(v) for v in sections.values())} 条材料句，"
            f"分布在 {sum(1 for v in sections.values() if v)}/{len(SECTIONS)} 个章节。"
            f".docx 包含真实 OOXML 样式（Title/Heading1）、编号定义、数值表格和 TOC 域，"
            f"可在 Word 中直接编辑续写。"
        ),
        "sections": {k: len(v) for k, v in sections.items()},
    }

    # 14. Determine status
    has_any_material = bool(blocks)
    status = "answered" if has_any_material else "insufficient_evidence"
    if missing and has_any_material:
        status = "partial"

    # 15. Write result.json (output_contract fields)
    payload = {
        "case_id": case_id,
        "deliverable": deliverable,
        "preserved_facts": {
            "summary": preserved_facts_summary,
            "items": preserved_facts[:60],
        },
        "evidence_map": evidence_map[:100],
        "change_log": change_log,
        # Additional metadata (not in contract but useful)
        "status": status,
        "sources_processed": [{"name": n} for n in notes],
        "uncertainties": [f"章节缺材料依据：{', '.join(missing)}"] if missing else [],
        "limitations": [
            "正文只复述材料事实，不做推理型论证；材料缺失处显式标注而非补写",
            "docx 的目录(TOC)字段需在 Word 中更新一次才显示页码",
            "PDF 无文本层时无法处理（需 OCR），会标记为 partial",
        ],
    }
    (out / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 16. Self-check
    with zipfile.ZipFile(out / "manuscript.docx") as z:
        parts = z.namelist()
        wellformed = b"<w:document" in z.read("word/document.xml")
    sc = {
        "blocks": len(blocks),
        "sections_with_material": sum(1 for v in sections.values() if v),
        "sections_total": len(SECTIONS),
        "docx_parts": len(parts),
        "docx_wellformed": wellformed,
        "claim_map_rows": len(map_rows) - 1,
        "key_numbers": len(key_numbers),
        "evidence_map_entries": len(evidence_map),
        "case_id": case_id,
        "output_contract_fields": ["case_id", "deliverable", "preserved_facts",
                                    "evidence_map", "change_log"],
    }
    (out / "SELFCHECK.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(sc, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
