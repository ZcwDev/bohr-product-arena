#!/usr/bin/env python3
"""Academic Paper Writing Skill — single-shot entry point.

Called by the shell wrapper `run.py` after interpreter detection.
Reads a case directory, produces an editable manuscript + previews + result.json.

Design rules
  * never exception-exit: every third-party import is try/except, every
    subprocess call is guarded, every missing artifact gets a note, not a crash
  * contract-driven: result.json shape comes from the case's output_contract
    or falls back to the writing-family default
  * evidence-bound: every claim in evidence_map has a locator that resolves
    to real bytes in the input material
  * honest: numbers come from the material, never fabricated; if material is
    unreadable we say so in status, not invent content
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ──────────────────────────────────────────────────────────────────────────────
# Third-party imports (best-effort; set flags so downstream code can degrade)
# ──────────────────────────────────────────────────────────────────────────────
HAS_FITZ = False
HAS_MATPLOTLIB = False
HAS_DOCX = False
HAS_PPTX = False
HAS_PIL = False
HAS_CAIROSVG = False

try:
    import fitz  # type: ignore
    HAS_FITZ = True
except Exception:
    pass

try:
    import matplotlib  # type: ignore
    HAS_MATPLOTLIB = True
except Exception:
    pass

try:
    import docx as _docx_mod  # type: ignore
    HAS_DOCX = True
except Exception:
    pass

try:
    import pptx as _pptx_mod  # type: ignore
    HAS_PPTX = True
except Exception:
    pass

try:
    from PIL import Image as _PILImage  # type: ignore
    HAS_PIL = True
except Exception:
    pass

try:
    import cairosvg  # type: ignore
    HAS_CAIROSVG = True
except Exception:
    pass

PYTHON_PATH = sys.executable
PYTHON_VERSION = sys.version.split()[0]

# ──────────────────────────────────────────────────────────────────────────────
# Local module imports (our own scripts — always available)
# ──────────────────────────────────────────────────────────────────────────────
import make_blocks as make_blocks_mod  # noqa: E402
import contract as contract_mod  # noqa: E402
import evidence as evidence_mod  # noqa: E402
import paper as paper_mod  # noqa: E402
import ooxml as ooxml_mod  # noqa: E402

# render/preview: import only when needed (they pull matplotlib at top level)
try:
    import render as render_mod  # noqa: E402
    HAS_RENDER = True
except Exception:
    HAS_RENDER = False

try:
    import preview as preview_mod  # noqa: E402
    HAS_PREVIEW = True
except Exception:
    HAS_PREVIEW = False

try:
    import text_checks as text_checks_mod  # noqa: E402
    HAS_TEXT_CHECKS = True
except Exception:
    HAS_TEXT_CHECKS = False


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────
def safe_json_load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def safe_run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 127, str(exc)[:300]


def discover_case(input_dir: str) -> tuple[str | None, dict]:
    """Find the case JSON in the input directory.
    
    Priority:
    1. Any *.json with a "case_id" key
    2. case.json
    3. The only .json file present
    """
    d = Path(input_dir)
    jsons = sorted(d.glob("*.json"))
    if not jsons:
        return None, {}
    # First pass: look for one with case_id
    for p in jsons:
        data = safe_json_load(str(p))
        if data and isinstance(data, dict) and "case_id" in data:
            return str(p), data
    # Second pass: case.json by name
    for p in jsons:
        if p.stem.lower() in ("case", "input", "task"):
            data = safe_json_load(str(p))
            if data and isinstance(data, dict):
                return str(p), data
    # Third: the only one
    if len(jsons) == 1:
        data = safe_json_load(str(jsons[0]))
        if data and isinstance(data, dict):
            return str(jsons[0]), data
    return str(jsons[0]), safe_json_load(str(jsons[0])) or {}


def discover_materials(input_dir: str, case_path: str | None) -> list[str]:
    """Find all material files (PDF, md, txt, docx, csv, json) in the input dir.
    
    Excludes:
    - example_*.json (example input/result files that are not research materials)
    - case.json (the case definition itself)
    """
    d = Path(input_dir)
    exts = {".pdf", ".md", ".markdown", ".txt", ".docx", ".csv", ".tsv",
            ".json", ".jsonl", ".tex", ".bib", ".html", ".xml", ".yaml", ".yml"}
    exclude_patterns = {"example_input.json", "example_result.json", "case.json"}
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            if case_path and str(p) == case_path:
                continue
            if p.name in exclude_patterns or p.name.startswith("example_"):
                continue
            out.append(str(p))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ──────────────────────────────────────────────────────────────────────────────

def index_materials(materials: list[str], work_dir: str) -> list[dict]:
    """Index each material file into blocks with locators. Returns list of block data."""
    all_blocks = []
    for mat in materials:
        ext = os.path.splitext(mat)[1].lower()
        out_path = os.path.join(work_dir, f"blocks_{os.path.basename(mat)}.json")
        try:
            if ext == ".pdf":
                # Use pdf_text via subprocess for isolation
                code, log = safe_run([
                    PYTHON_PATH, os.path.join(HERE, "pdf_text.py"),
                    "extract", mat, "-o", out_path
                ])
                if code == 0 and os.path.exists(out_path):
                    data = safe_json_load(out_path)
                    if data:
                        all_blocks.append(data)
                        continue
                # Fallback: try make_blocks
                data = make_blocks_mod.build(mat)
            elif ext == ".docx":
                data = make_blocks_mod.index_docx(mat)
            else:
                data = make_blocks_mod.index_text(mat)
            if data and data.get("blocks"):
                json.dump(data, open(out_path, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                all_blocks.append(data)
        except Exception as exc:
            all_blocks.append({"source": os.path.basename(mat), "engine": "error",
                               "blocks": [], "warnings": [str(exc)[:200]]})
    return all_blocks


def extract_facts(all_blocks: list[dict]) -> list[dict]:
    """Extract numbers, terms, conditions from all blocks."""
    # Use text_checks regexes which are the canonical citation/number regexes
    cite_re = None
    num_re = evidence_mod.NUMBER_RE
    if HAS_TEXT_CHECKS:
        cite_re = text_checks_mod.CITE_RE

    facts = []
    for bd in all_blocks:
        for blk in bd.get("blocks", []):
            text = blk.get("text", "")
            locator = blk.get("locator", "")
            # Numbers with units
            for m in num_re.finditer(text):
                tok = m.group(0).strip()
                if len(tok) > 2:
                    facts.append({"type": "number", "value": tok,
                                  "locator": locator, "source": bd.get("source", "")})
            # Citation markers
            if cite_re is not None:
                for m in cite_re.finditer(text):
                    marker = m.group(0)
                    facts.append({"type": "citation", "value": marker,
                                  "locator": locator, "source": bd.get("source", "")})
    return facts


def plan_sections(case: dict, all_blocks: list[dict]) -> dict:
    """Determine the paper genre and produce a section plan."""
    # detect_genre takes a dict and returns (genre, source)
    # It checks case["genre"], case["task_instruction"], etc.
    genre, genre_source = paper_mod.detect_genre(case)
    template_key = genre if genre in paper_mod.TEMPLATES else "research"

    # Use explicit sections from case if provided, else built-in template
    explicit = case.get("sections") or case.get("required_sections")
    if isinstance(explicit, list) and explicit:
        sections = []
        for i, item in enumerate(explicit, start=1):
            if isinstance(item, dict):
                sections.append((
                    item.get("id", f"s{i}"),
                    item.get("heading", item.get("title", f"Section {i}")),
                    item.get("evidence_need", "result"),
                    int(item.get("word_budget", 0))
                ))
            else:
                sections.append((f"s{i}", str(item), "result", 0))
    else:
        sections = [(sid, h, n, w) for sid, h, _p, n, w in paper_mod.TEMPLATES[template_key]]

    # Add case-specific output requirements
    extra = {}
    for key in ("target_journal", "word_limit", "citation_style", "language",
                "audience", "format_requirements"):
        if case.get(key):
            extra[key] = case[key]
    return {"genre": template_key, "sections": sections, "extra": extra,
            "genre_source": genre_source}


def assemble_draft(plan: dict, all_blocks: list[dict], facts: list[dict],
                   work_dir: str, case: dict) -> dict:
    """Assemble a draft from plan + blocks + facts.
    
    Returns {"draft_path": ..., "draft_text": ..., "spec": ...}
    where spec uses the "blocks" format that ooxml.build_docx and preview.doc_png expect.
    """
    draft_path = os.path.join(work_dir, "draft.md")
    spec_path = os.path.join(work_dir, "spec.json")

    # Build the draft as markdown
    sections = plan.get("sections", [])
    lines = []
    # Also build spec blocks in parallel (format: {"type": "h1|h2|p|bullet", "text": ...})
    spec_blocks = []

    # Title
    case_id = case.get("case_id", "untitled")
    title = case.get("title", case.get("topic", f"Manuscript — {case_id}"))
    lines.append(f"# {title}")
    spec_blocks.append({"type": "h1", "text": title})

    # Each section: pull blocks that match evidence_need
    section_content = {}
    all_text_blocks = [b for bd in all_blocks for b in bd.get("blocks", [])]
    # Track which blocks were used so later sections don't reuse the same block
    used_locators: set[str] = set()

    for sec_id, heading, evidence_need, word_budget in sections:
        lines.append("")
        lines.append(f"## {heading}")
        lines.append("")
        spec_blocks.append({"type": "h2", "text": heading})

        if evidence_need == "none":
            lines.append("*[此节待人工补充]*")
            spec_blocks.append({"type": "p", "text": "[此节待人工补充]"})
            section_content[sec_id] = True
            continue

        if evidence_need == "citation":
            # Collect references from material
            ref_blocks = [b for b in all_text_blocks
                          if any(kw in b.get("text", "").lower()
                                 for kw in ("reference", "参考文献", "bibliography",
                                            "[1]", "[2]", "\\bibitem"))]
            if ref_blocks:
                for rb in ref_blocks[:10]:
                    text = rb["text"][:300]
                    lines.append(text)
                    loc = rb.get("locator", "")
                    lines.append(f"  <!-- locator: {loc} -->")
                    spec_blocks.append({"type": "p", "text": text})
                    used_locators.add(loc)
            else:
                lines.append("*[参考文献待从材料中提取]*")
                spec_blocks.append({"type": "p", "text": "[参考文献待从材料中提取]"})
            section_content[sec_id] = True
            continue

        # Match blocks by evidence_need using paper.NEED_CUES regex
        cue_re = paper_mod.NEED_CUES.get(evidence_need)
        matched = []
        for blk in all_text_blocks:
            loc = blk.get("locator", "")
            if loc in used_locators:
                continue
            text = blk.get("text", "")
            if not text.strip():
                continue
            score = 0
            if cue_re is not None:
                hits = len(cue_re.findall(text))
                score = hits
            if score > 0:
                matched.append((score, blk))

        if matched:
            matched.sort(key=lambda x: -x[0])
            for _, blk in matched[:5]:
                text = blk.get("text", "").strip()
                loc = blk.get("locator", "")
                if text:
                    lines.append(text)
                    lines.append(f"  <!-- locator: {loc} -->")
                    lines.append("")
                    spec_blocks.append({"type": "p", "text": text})
                    used_locators.add(loc)
            section_content[sec_id] = True
        else:
            # No matching blocks — include unused non-empty blocks as general content
            fallback = [b for b in all_text_blocks
                        if b.get("text", "").strip() and b.get("locator", "") not in used_locators][:3]
            if fallback:
                for blk in fallback:
                    text = blk["text"][:400]
                    loc = blk.get("locator", "")
                    lines.append(text)
                    lines.append(f"  <!-- locator: {loc} (fallback) -->")
                    lines.append("")
                    spec_blocks.append({"type": "p", "text": text})
                    used_locators.add(loc)
                section_content[sec_id] = True
            else:
                lines.append(f"*[此节无匹配材料，需人工撰写 — {evidence_need}]*")
                spec_blocks.append({"type": "p", "text": f"[此节无匹配材料，需人工撰写 — {evidence_need}]"})
                section_content[sec_id] = False

    draft_text = "\n".join(lines)
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(draft_text)

    # ── Build OOXML-rich spec ─────────────────────────────────────────────
    # Rebuild spec_blocks from scratch with rich block types
    rich_blocks: list[dict] = []

    # (title, author, abstract, toc handled at spec level by ooxml.py — not as blocks)
    author = case.get("author", "Skill Pipeline")

    # Build abstract from first non-placeholder paragraph
    abstract_lines = []
    for sb in spec_blocks:
        if sb.get("type") == "p" and sb.get("text", "").strip() and not sb["text"].startswith("["):
            abstract_lines.append(sb["text"])
            if len(abstract_lines) >= 2:
                break
    abstract_text = " ".join(abstract_lines) if abstract_lines else "摘要待人工完善。"

    # 5. Build sections with rich block types
    section_statuses: list[tuple[str, str]] = []  # (heading, status)

    # Walk through spec_blocks and convert to rich types
    current_heading = ""
    current_evidence_need = "none"
    paragraph_count = 0
    section_paragraphs: list[str] = []

    for sb in spec_blocks:
        btype = sb.get("type", "p")
        text = sb.get("text", "")

        if btype == "h1":
            # Title heading — skip (already added as Title block)
            continue
        elif btype == "h2":
            # Flush previous section's paragraphs as bullet list if applicable
            if section_paragraphs and len(section_paragraphs) >= 2:
                # First paragraph as regular text, rest as bullets
                rich_blocks.append({"type": "p", "text": section_paragraphs[0]})
                for ep in section_paragraphs[1:]:
                    rich_blocks.append({"type": "bullet", "text": ep})
            elif section_paragraphs:
                for ep in section_paragraphs:
                    rich_blocks.append({"type": "p", "text": ep})
            section_paragraphs = []

            current_heading = text
            rich_blocks.append({"type": "h2", "text": text})
            paragraph_count = 0

            # Find matching section to check status
            for sid, h, en, wb in sections:
                if h == text:
                    current_evidence_need = en
                    break
            continue

        # It's a paragraph block
        paragraph_count += 1
        if text.startswith("[此节待人工补充]") or text.startswith("[此节无匹配材料"):
            # Revision marks for gaps
            rich_blocks.append({
                "type": "revision_del",
                "text": f"[原文待人工撰写: {current_heading}]",
                "author": "Skill Reviewer"
            })
            rich_blocks.append({
                "type": "revision_ins",
                "text": f"[待补充: {current_heading} — 需{current_evidence_need}类证据]",
                "author": "Skill Reviewer"
            })
            section_statuses.append((current_heading, "gap"))
        elif text.startswith("[参考文献待从材料中提取]"):
            rich_blocks.append({"type": "p", "text": text})
        else:
            section_paragraphs.append(text)
            section_statuses.append((current_heading, "filled"))

    # Flush final section paragraphs
    if section_paragraphs and len(section_paragraphs) >= 2:
        rich_blocks.append({"type": "p", "text": section_paragraphs[0]})
        for ep in section_paragraphs[1:]:
            rich_blocks.append({"type": "bullet", "text": ep})
    elif section_paragraphs:
        for ep in section_paragraphs:
            rich_blocks.append({"type": "p", "text": ep})

    # 6. Page break before appendix material
    rich_blocks.append({"type": "page_break"})

    # 7. Material index summary table
    index_rows = []
    for bd in all_blocks:
        for blk in bd.get("blocks", []):
            loc = blk.get("locator", "")
            text_snip = blk.get("text", "")[:60]
            if loc and text_snip:
                # Find which section uses this locator
                section_label = ""
                for sid, h, en, wb in sections:
                    section_label = h
                    break  # use first section as label
                index_rows.append((loc, section_label, text_snip))
    if index_rows:
        rich_blocks.append({"type": "h2", "text": "Appendix: Material Index"})
        table_rows = [["Locator", "Section", "Snippet"]]
        for loc, heading, snippet in index_rows[:8]:
            table_rows.append([loc, heading, snippet[:50]])
        rich_blocks.append({"type": "table", "rows": table_rows})

    # 8. Numbered chapter structure list
    if len(sections) >= 3:
        rich_blocks.append({"type": "h2", "text": "Chapter Structure"})
        for i, (sid, heading, en, wb) in enumerate(sections, 1):
            status = "✓" if any(h == heading and s == "filled" for h, s in section_statuses) else "○"
            rich_blocks.append({"type": "number", "text": f"{status} {heading}"})

    spec = {
        "title": title,
        "author": author,
        "abstract": abstract_text,
        "toc": True,
        "blocks": rich_blocks,
    }

    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)

    return {"draft_path": draft_path, "draft_text": draft_text, "spec_path": spec_path,
            "spec": spec, "section_count": len(sections),
            "sections_filled": sum(1 for s in section_content.values() if s)}


def generate_docx(spec: dict, out_path: str) -> dict:
    """Generate a .docx from the spec. Always succeeds (stdlib fallback)."""
    try:
        info = ooxml_mod.build_docx(spec, out_path)
        return {"ok": True, "engine": "ooxml_stdlib", "path": out_path, **info}
    except Exception as exc:
        _log(f"ooxml_mod.build_docx failed: {exc}; writing minimal fallback")
        try:
            _write_minimal_docx(spec, out_path)
            return {"ok": True, "engine": "stdlib_fallback", "path": out_path,
                    "bytes": os.path.getsize(out_path)}
        except Exception as exc2:
            return {"ok": False, "engine": "none", "error": str(exc2)[:200]}


def _write_minimal_docx(spec: dict, out_path: str):
    """Write a minimal valid .docx (stdlib only). Uses "blocks" spec format."""
    import zipfile
    title = spec.get("title", "Document")
    blocks = spec.get("blocks", [])
    ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/package/2006/relationships"
    ns_r2 = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    body_parts = [
        f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
        f'<w:r><w:rPr><w:b/></w:rPr>'
        f'<w:t xml:space="preserve">{_xml_escape(title)}</w:t></w:r></w:p>'
    ]
    for b in blocks:
        text = b.get("text", "")
        kind = (b.get("type") or "p").lower()
        if kind in ("h1", "title"):
            body_parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                f'<w:r><w:rPr><w:b/></w:rPr>'
                f'<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')
        elif kind == "h2":
            body_parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                f'<w:r><w:rPr><w:b/></w:rPr>'
                f'<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')
        else:
            body_parts.append(
                f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')

    body = "".join(body_parts)
    doc_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="{ns_w}"><w:body>{body}</w:body></w:document>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{ns_r}">'
            f'<Relationship Id="rId1" Type="{ns_r2}/officeDocument" '
            f'Target="word/document.xml"/>'
            f'</Relationships>')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def generate_preview(spec: dict, draft_text: str, out_dir: str) -> dict:
    """Generate preview PNGs. Falls back to a simple text-rendered PNG."""
    previews = []

    # Try preview.py doc command
    if HAS_PREVIEW:
        try:
            info = preview_mod.doc_png(spec, out_dir)
            if info.get("pages"):
                return {"ok": True, "engine": "preview_matplotlib", "pages": info["pages"]}
        except Exception:
            pass

    # Fallback: matplotlib direct
    if HAS_MATPLOTLIB:
        try:
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            os.makedirs(out_dir, exist_ok=True)

            # Render draft as a formatted page
            preview_mod.use_cjk_font()
            lines = draft_text.split("\n")
            page_lines = []
            page_num = 0
            for i, line in enumerate(lines):
                page_lines.append(line)
                if len(page_lines) >= 40 or i == len(lines) - 1:
                    page_num += 1
                    fig, ax = plt.subplots(figsize=(8.27, 11.69))
                    ax.set_xlim(0, 1)
                    ax.set_ylim(0, 1)
                    ax.axis("off")
                    y = 0.96
                    for pl in page_lines:
                        if pl.startswith("# "):
                            ax.text(0.05, y, pl[2:], fontsize=16, fontweight="bold",
                                    transform=ax.transAxes, va="top")
                            y -= 0.03
                        elif pl.startswith("## "):
                            ax.text(0.05, y, pl[3:], fontsize=13, fontweight="bold",
                                    color="#2F6FED", transform=ax.transAxes, va="top")
                            y -= 0.025
                        elif pl.startswith("<!--"):
                            continue
                        elif pl.strip():
                            text = pl[:90] + ("..." if len(pl) > 90 else "")
                            ax.text(0.05, y, text, fontsize=9, transform=ax.transAxes, va="top")
                            y -= 0.018
                    png_path = os.path.join(out_dir, f"page_{page_num:02d}.png")
                    fig.savefig(png_path, dpi=150, bbox_inches="tight")
                    plt.close(fig)
                    previews.append(png_path)
                    page_lines = []

            if previews:
                return {"ok": True, "engine": "matplotlib_direct", "pages": previews}
        except Exception as exc:
            return {"ok": False, "engine": "matplotlib_direct", "error": str(exc)[:200]}

    # No matplotlib either — write a text placeholder note (not a placeholder image)
    note_path = os.path.join(out_dir, "no_preview_engine.txt")
    os.makedirs(out_dir, exist_ok=True)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write("No rendering engine available (matplotlib not installed).\n")
        f.write("The editable .docx is the primary deliverable.\n")
    return {"ok": False, "engine": "none", "note": note_path,
            "error": "no matplotlib or preview engine available"}


def run_number_check(draft_text: str, all_blocks: list[dict], work_dir: str) -> dict:
    """Verify that numbers from source material are preserved in the draft."""
    if not HAS_TEXT_CHECKS:
        return {"ok": False, "note": "text_checks module unavailable"}

    # Collect source numbers
    source_text = "\n".join(b.get("text", "") for bd in all_blocks for b in bd.get("blocks", []))

    src_nums = evidence_mod.NUMBER_RE.findall(source_text)
    draft_nums = evidence_mod.NUMBER_RE.findall(draft_text)

    from collections import Counter
    src_counter = Counter(t.strip() for t in src_nums if len(t.strip()) > 1)
    draft_counter = Counter(t.strip() for t in draft_nums if len(t.strip()) > 1)

    missing = {k: v for k, v in src_counter.items() if draft_counter.get(k, 0) < v}
    preserved = {k: min(v, draft_counter.get(k, 0)) for k, v in src_counter.items()
                 if draft_counter.get(k, 0) > 0}

    return {
        "source_numbers": len(src_counter),
        "draft_numbers": len(draft_counter),
        "preserved": preserved,
        "preserved_count": len(preserved),
        "missing_in_draft": missing,
        "missing_count": len(missing),
        "ok": len(missing) == 0
    }


def build_evidence_map(all_blocks: list[dict], facts: list[dict], draft_text: str) -> list[dict]:
    """Build evidence_map: key claims in the draft -> source locators."""
    evidence_map = []
    seen_locators = set()

    for fact in facts[:50]:  # cap at 50 evidence entries
        locator = fact.get("locator", "")
        value = fact.get("value", "")
        fact_type = fact.get("type", "")

        if locator in seen_locators and fact_type != "citation":
            continue

        # Verify the value appears in the draft
        if value and value in draft_text:
            evidence_map.append({
                "claim": f"{fact_type}: {value}" if fact_type else value,
                "locator": locator,
                "quote": value[:120],
                "verified_in_draft": True,
                "source": fact.get("source", "")
            })
            seen_locators.add(locator)
        elif value:
            evidence_map.append({
                "claim": f"{fact_type}: {value}" if fact_type else value,
                "locator": locator,
                "quote": value[:120],
                "verified_in_draft": False,
                "source": fact.get("source", ""),
                "note": "value from material not found in draft"
            })

    return evidence_map[:30]


def build_preserved_facts(facts: list[dict], draft_text: str) -> list[dict]:
    """Build preserved_facts: numbers/conditions that must remain unchanged."""
    preserved = []
    seen = set()
    for fact in facts:
        if fact.get("type") != "number":
            continue
        val = fact.get("value", "").strip()
        if not val or len(val) < 2 or val in seen:
            continue
        seen.add(val)
        preserved.append({
            "fact": val,
            "source": fact.get("locator", ""),
            "changed": False,
            "in_draft": val in draft_text
        })
    return preserved[:30]


def build_change_log(draft_info: dict, docx_info: dict, preview_info: dict,
                     number_check: dict) -> list[dict]:
    """Build change_log: what the pipeline did."""
    log = []
    log.append({
        "action": "indexed_materials",
        "reason": "extract text and locators from input files",
        "human_check": False
    })
    log.append({
        "action": "planned_sections",
        "reason": f"genre-based section template ({draft_info.get('genre', 'research')})",
        "human_check": False
    })
    log.append({
        "action": "assembled_draft",
        "reason": f"matched blocks to {draft_info.get('section_count', 0)} sections",
        "human_check": True
    })
    log.append({
        "action": "generated_docx",
        "reason": f"editable deliverable via {docx_info.get('engine', 'unknown')}",
        "human_check": False
    })
    log.append({
        "action": "generated_preview",
        "reason": f"preview via {preview_info.get('engine', 'unknown')}",
        "human_check": False
    })
    log.append({
        "action": "number_preservation_check",
        "reason": f"{number_check.get('preserved_count', 0)}/{number_check.get('source_numbers', 0)} numbers preserved",
        "human_check": number_check.get("missing_count", 0) > 0
    })
    return log


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline(input_dir: str, output_dir: str) -> int:
    os.makedirs(output_dir, exist_ok=True)
    work_dir = os.path.join(output_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    notes = []
    engine_info = {
        "python": PYTHON_PATH,
        "python_version": PYTHON_VERSION,
        "has_fitz": HAS_FITZ,
        "has_matplotlib": HAS_MATPLOTLIB,
        "has_docx_lib": HAS_DOCX,
        "has_pil": HAS_PIL,
        "has_cairosvg": HAS_CAIROSVG,
    }

    # ── Step 1: Discover case ──────────────────────────────────────────────
    case_path, case = discover_case(input_dir)
    case_id = case.get("case_id", "")
    if not case_id:
        case_id = "unknown-case"
        notes.append("no case_id found in input; used 'unknown-case'")
    if not case_path:
        notes.append("no case JSON found in input directory")

    # Read output contract from case
    output_contract = case.get("output_contract", {})
    status_values = output_contract.get("status_values",
                      case.get("status_values",
                      ["answered", "partial", "insufficient_evidence", "input_error"]))

    # ── Step 2: Discover materials ─────────────────────────────────────────
    materials = discover_materials(input_dir, case_path)
    if not materials:
        notes.append("no material files found in input directory")

    # ── Step 3: Index materials ────────────────────────────────────────────
    all_blocks = index_materials(materials, work_dir) if materials else []
    total_blocks = sum(len(bd.get("blocks", [])) for bd in all_blocks)
    notes.append(f"indexed {len(materials)} material(s), {total_blocks} block(s)")

    if total_blocks == 0 and materials:
        notes.append("WARNING: materials present but no text extracted")

    # ── Step 4: Extract facts ──────────────────────────────────────────────
    facts = extract_facts(all_blocks)
    notes.append(f"extracted {len(facts)} fact(s)")

    # ── Step 5: Plan sections ──────────────────────────────────────────────
    plan = plan_sections(case, all_blocks)
    notes.append(f"plan: {plan.get('genre', 'unknown')} with {len(plan.get('sections', []))} sections")

    # ── Step 6: Assemble draft ─────────────────────────────────────────────
    draft_info = assemble_draft(plan, all_blocks, facts, work_dir, case)
    draft_info["genre"] = plan.get("genre", "research")
    notes.append(f"draft: {len(draft_info.get('draft_text', ''))} chars, "
                 f"{draft_info.get('section_count', 0)} sections")

    # ── Step 7: Generate docx ──────────────────────────────────────────────
    docx_path = os.path.join(output_dir, f"{case_id}.docx")
    docx_info = generate_docx(draft_info.get("spec", {}), docx_path)
    notes.append(f"docx: {docx_info.get('engine', 'unknown')}, "
                 f"ok={docx_info.get('ok', False)}")

    # ── Step 8: Generate preview ───────────────────────────────────────────
    preview_dir = os.path.join(output_dir, "preview")
    preview_info = generate_preview(draft_info.get("spec", {}),
                                    draft_info.get("draft_text", ""),
                                    preview_dir)
    notes.append(f"preview: {preview_info.get('engine', 'unknown')}, "
                 f"ok={preview_info.get('ok', False)}")

    # ── Step 9: Number preservation check ──────────────────────────────────
    number_check = run_number_check(draft_info.get("draft_text", ""),
                                    all_blocks, work_dir)
    notes.append(f"numbers: {number_check.get('preserved_count', 0)}/"
                 f"{number_check.get('source_numbers', 0)} preserved")

    # ── Step 10: Build result.json ─────────────────────────────────────────
    evidence_map = build_evidence_map(all_blocks, facts, draft_info.get("draft_text", ""))
    preserved_facts = build_preserved_facts(facts, draft_info.get("draft_text", ""))
    change_log = build_change_log(
        {**draft_info, **plan},
        docx_info, preview_info, number_check
    )

    # Determine status
    if not materials:
        status = "insufficient_evidence"
    elif total_blocks == 0:
        status = "input_error"
    elif docx_info.get("ok") and number_check.get("ok"):
        status = "answered"
    elif docx_info.get("ok"):
        status = "partial"
    else:
        status = "partial"

    # Normalize status to allowed values
    if status not in status_values:
        status = contract_mod.normalize(raw={"status": status}).get("status", "partial")
        if status not in status_values:
            status = "partial"

    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": {
            "path": os.path.relpath(docx_path, output_dir) if docx_info.get("ok") else None,
            "draft_path": os.path.relpath(draft_info["draft_path"], output_dir),
            "format": "docx",
            "draft_format": "markdown"
        },
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "notes": [],
    }

    # Append notes about demo/synthetic materials
    if case_id.upper().startswith("DEMO-") or case_id.upper().startswith("EXAMPLE"):
        result["notes"].append("本次演示使用自造示例材料")
    if not materials:
        result["notes"].append("No source materials found in input directory; deliverable is a structural template")
    if number_check.get("missing_count", 0) > 0:
        result["notes"].append(
            f"{number_check['missing_count']} source number(s) could not be verified in draft; human review required"
        )
    if not preview_info.get("ok"):
        result["notes"].append(f"Preview generation degraded: {preview_info.get('error', 'unknown')}")

    # Mark example runs explicitly for contract compliance
    if "EXAMPLE" in case_id.upper():
        result["example_run"] = True

    # Add limitations if not fully answered
    limitations = []
    if status != "answered":
        if not materials:
            limitations.append("No input materials found; deliverable is a template only")
        if total_blocks == 0:
            limitations.append("Materials present but no text could be extracted")
        if not docx_info.get("ok"):
            limitations.append(f"DOCX generation failed: {docx_info.get('error', 'unknown')}")
        if number_check.get("missing_count", 0) > 0:
            limitations.append(
                f"{number_check['missing_count']} source numbers not found in draft; "
                "human review needed"
            )
    if not preview_info.get("ok"):
        limitations.append(f"Preview generation degraded: {preview_info.get('error', 'unknown')}")
    if limitations:
        result["limitations"] = limitations

    # Engine metadata (for RUN.md)
    result["_engine"] = engine_info
    result["_notes"] = notes
    result["_materials"] = [os.path.basename(m) for m in materials]
    result["_block_count"] = total_blocks
    result["_preview_pages"] = preview_info.get("pages", [])

    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    # Also copy draft to output root
    draft_out = os.path.join(output_dir, "draft.md")
    if os.path.exists(draft_info["draft_path"]):
        shutil.copy2(draft_info["draft_path"], draft_out)

    # Write engine info for RUN.md
    engine_path = os.path.join(output_dir, "engine_info.json")
    with open(engine_path, "w", encoding="utf-8") as f:
        json.dump({**engine_info, "notes": notes, "materials": [os.path.basename(m) for m in materials],
                   "docx": docx_info, "preview": {k: v for k, v in preview_info.items() if k != "pages"},
                   "preview_pages": preview_info.get("pages", []),
                   "number_check": number_check,
                   "status": status, "case_id": case_id},
                  f, ensure_ascii=False, indent=1)

    print(json.dumps({
        "case_id": case_id,
        "status": status,
        "materials": len(materials),
        "blocks": total_blocks,
        "facts": len(facts),
        "docx_ok": docx_info.get("ok", False),
        "preview_ok": preview_info.get("ok", False),
        "numbers_preserved": number_check.get("preserved_count", 0),
        "evidence_entries": len(evidence_map),
        "output_dir": output_dir,
    }, ensure_ascii=False, indent=1))

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Academic Paper Writing Skill — single-shot entry point")
    ap.add_argument("--input", default="/input", help="input directory with case.json + materials")
    ap.add_argument("--output", default="/app/output", help="output directory")
    args = ap.parse_args()

    try:
        return run_pipeline(args.input, args.output)
    except Exception as exc:
        # Never exception-exit: write a minimal result.json and return 1
        tb = traceback.format_exc()
        output_dir = args.output
        os.makedirs(output_dir, exist_ok=True)
        result = {
            "case_id": "error",
            "status": "input_error",
            "deliverable": None,
            "preserved_facts": [],
            "evidence_map": [],
            "change_log": [{"action": "pipeline_error", "reason": str(exc)[:200],
                            "human_check": True}],
            "limitations": [f"Pipeline error: {str(exc)[:200]}",
                            f"Traceback: {tb[:500]}"]
        }
        with open(os.path.join(output_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"ERROR: {exc}", file=sys.stderr)
        print(tb[:500], file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
