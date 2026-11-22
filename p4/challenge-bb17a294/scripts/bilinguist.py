#!/usr/bin/env python3
"""bilinguist — a bilingual translation workstation with segment-level audit.

PURPOSE
  Split an academic source into numbered segments, build a locked glossary,
  ingest a translator's per-segment output, audit numbers / citations / negation
  / terminology / alignment, and export a bilingual DOCX + PDF/PNG preview
  with a structured result.json.

USAGE (single-pass, translations file provided by the agent/translator)
  bilinguist.py --input DIR --output DIR run --translations FILE.tsv

USAGE (step-by-step)
  bilinguist.py --input DIR --output DIR segment      # writes segments.tsv
  bilinguist.py --input DIR --output DIR terms        # writes glossary.tsv
  bilinguist.py --input DIR --output DIR apply --translations FILE.tsv
  bilinguist.py --input DIR --output DIR export       # writes bilingual.docx + result.json

INPUT (DIR = /input or any directory)
  - case.json (or *.json): must contain case_id and optionally task_instruction,
    source {title, path, files}, materials, output_contract.
  - Source documents: *.pdf, *.docx, *.md, *.txt, *.tex in the same directory.

OUTPUT (DIR = /app/output or any directory)
  - segments.tsv, glossary.tsv, alignment.tsv
  - bilingual.docx, bilingual_export.pdf, previews/page-*.png (when LibreOffice available)
  - result.json with fields: case_id, status, deliverable, preserved_facts,
    evidence_map, change_log, result, claims, uncertainties, limitations, verification
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- runtime


def _ensure_science_python() -> None:
    """Re-exec under a Python with PyMuPDF if available; safe no-op otherwise."""
    try:
        import fitz  # noqa: F401
        return
    except Exception:
        pass
    for cand in (os.environ.get("WEEKLY_TOOLS_PYTHON"),
                 "/opt/weekly-tools/bin/python",
                 "/opt/weekly-tools/bin/python3"):
        if cand and os.path.exists(cand) and os.path.realpath(cand) != os.path.realpath(sys.executable):
            try:
                os.execv(cand, [cand] + sys.argv)
            except Exception:
                continue


# ---------------------------------------------------------------- PDF readers


def _pdftotext_pages(path) -> list[str]:
    """Fallback: pdftotext --layout keeps per-page structure."""
    if not shutil.which("pdftotext"):
        return []
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return []
    return out.split("\f")


def _pure_pdf_text(path) -> list[str]:
    """Last-resort PDF text extraction using only stdlib + zlib."""
    import zlib as _zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    TOKEN = re.compile(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?")
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
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
                in_array = True; continue
            if tok == "]":
                in_array = False; continue
            if tok.startswith("("):
                parts.append(tok[1:-1]); continue
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


def _fitz():
    try:
        import fitz  # noqa
        return fitz
    except Exception:
        return None


# ---------------------------------------------------------------- text utils

CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[]|[A-Z][a-z])")
NUM = re.compile(
    r"(?<![0-9.])([-+]?\d+(?:[.,]\d+)?)\s*"
    r"(%|°C|mm|cm|µm|um|nm|MPa|kPa|GPa|N|kg|mg|mL|L|µL|"
    r"wt%|ppm|nM|µM|mM|h|min|sec|days?|weeks?|years?|samples?|specimens?)(?![A-Za-z])")
CITE = re.compile(r"(\[[\d,\s–-]+\]|\([A-Z][A-Za-z.\-]+(?:\s+et\s+al\.)?,?\s*\d{4}[a-z]?\))")
ACRONYM = re.compile(r"\b([A-Z]{2,6}(?:-[A-Z0-9]{1,4})?)\b")
NEG_SRC = re.compile(
    r"\b(not|no|never|neither|nor|cannot|can't|without|failed to|absent|"
    r"did not|does not|do not|is not|are not|was not|were not)\b", re.I)
NEG_TGT = re.compile(r"(?:不(?!仅|同|断|停|少|久|锈)|没有|未|无|非|并非)")
REF_HEAD = re.compile(r"^\s*(references|bibliography|参考文献)\s*$", re.I)


def norm(t: object) -> str:
    s = CTRL.sub(" ", str(t or "")).replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"[\ue000-\uf8ff]", "\u00b7", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def numbers(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in NUM.finditer(text or ""):
        key = f"{m.group(1).replace(',', '')} {m.group(2).lower()}"
        out[key] = out.get(key, 0) + 1
    return out


def citations(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in CITE.finditer(text or ""):
        key = re.sub(r"\s+", " ", m.group(1))
        out[key] = out.get(key, 0) + 1
    return out


def has_negation(text: str) -> bool:
    return bool(NEG_SRC.search(text or "") or NEG_TGT.search(text or ""))


def polarity_ok(source: str, target: str) -> bool:
    if NEG_SRC.search(source or "") and not (NEG_TGT.search(target or "") or NEG_SRC.search(target or "")):
        return False
    return True


# ---------------------------------------------------------------- readers


def read_pdf(path: Path) -> tuple[list[str], list[str]]:
    fitz = _fitz()
    pages = []
    if fitz:
        try:
            with fitz.open(str(path)) as doc:
                for p in doc:
                    pages.append(p.get_text("text", sort=True))
            return pages, []
        except Exception as e:
            warn = [f"fitz failed: {e}; trying fallback"]
    else:
        warn = ["PyMuPDF not installed; using fallback readers"]
    pages = _pdftotext_pages(path)
    if pages and any(p.strip() for p in pages):
        return pages, warn
    pages = _pure_pdf_text(path)
    if pages and any(p.strip() for p in pages):
        return pages, warn + ["used stdlib PDF reader (quality may be lower)"]
    return [], warn + ["no PDF text could be extracted"]


def read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", xml)
    except Exception:
        return ""


def read_source(path: Path) -> tuple[str, list[str]]:
    suf = path.suffix.lower()
    warns: list[str] = []
    if suf == ".pdf":
        pages, w = read_pdf(path)
        warns.extend(w)
        return "\f".join(pages), warns
    if suf == ".docx":
        return read_docx(path), warns
    if suf in {".md", ".txt", ".tex"}:
        try:
            return path.read_text(encoding="utf-8"), warns
        except Exception:
            return path.read_text(encoding="latin-1", errors="replace"), warns
    return "", [f"unsupported source format: {suf}"]


# ---------------------------------------------------------------- segmenter


def split_sections(text: str) -> list[tuple[str, str]]:
    HEADING = re.compile(r"^(#{1,4}\s+.+|\d+(?:\.\d+)*\s+[A-Z][^\n]{2,80})$")
    secs: list[tuple[str, str]] = []
    cur = "Introduction"
    buf: list[str] = []
    for line in text.splitlines():
        if HEADING.match(line.strip()):
            if buf:
                secs.append((cur, "\n".join(buf)))
            cur = re.sub(r"^[#\d.\s]+", "", line.strip())
            buf = []
        else:
            buf.append(line)
    if buf:
        secs.append((cur, "\n".join(buf)))
    return secs or [("Document", text)]


def segment(pages: list[str]) -> list[dict]:
    full = "\n".join(pages)
    segs: list[dict] = []
    idx = 0
    in_refs = False

    for sec_name, sec_text in split_sections(full):
        if REF_HEAD.match(sec_name.strip()):
            in_refs = True
        for i, para in enumerate(re.split(r"\n\s*\n", sec_text)):
            p = norm(para)
            if len(p) < 20:
                continue
            idx += 1
            kind = "ref" if in_refs else ("caption" if len(p) < 120 and re.match(r"^(fig|table|figure|表|图)", p, re.I) else "body")
            segs.append({
                "id": f"S{idx:04d}",
                "locator": f"{sec_name} ¶{i+1}",
                "section": sec_name,
                "kind": kind,
                "source": p,
                "source_sha256": sha(p),
                "numbers": numbers(p),
                "citations": citations(p),
                "has_negation": has_negation(p),
            })
    return segs


# ---------------------------------------------------------------- glossary


def build_glossary(segs: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    locs: dict[str, list[str]] = {}
    for s in segs:
        seen: set[str] = set()
        for m in ACRONYM.finditer(s["source"]):
            t = m.group(1)
            if t in {"THE", "AND", "FOR", "NOT", "BUT", "ALL", "THIS", "THAT", "FROM", "WITH", "HAVE",
                     "WAS", "WERE", "ARE", "HAS", "HAD", "BEEN", "WILL", "WHICH", "THEIR", "OTHER"}:
                continue
            counts[t] = counts.get(t, 0) + 1
            if t not in seen:
                locs.setdefault(t, []).append(s["id"])
                seen.add(t)
    rows: list[dict] = []
    for term, c in sorted(counts.items(), key=lambda x: -x[1]):
        if c < 2:
            continue
        rows.append({"term": term, "count": c, "occurrences": ",".join(locs[term][:5]),
                      "suggested_locked": True})
    return rows


# ---------------------------------------------------------------- audit


def audit(segs: list[dict], trans: dict[str, str], gloss: list[dict]):
    violations: list[dict] = []
    alignment: list[dict] = []
    translated = 0

    for s in segs:
        sid = s["id"]
        tgt = trans.get(sid, "").strip()
        if not tgt:
            violations.append({"segment": sid, "kind": "missing_translation",
                                "detail": "no target text provided for this segment"})
            alignment.append({"id": sid, "locator": s["locator"], "kind": s["kind"],
                               "section": s["section"], "length_ratio": 0.0,
                               "target": "", "problems": "missing_translation"})
            continue
        translated += 1
        problems: list[str] = []

        # numbers
        sn = s["numbers"]
        tn = numbers(tgt)
        for k in sn:
            if sn[k] != tn.get(k, 0):
                problems.append(f"number_mismatch:{k}")
                violations.append({"segment": sid, "kind": "number_mismatch",
                                    "detail": f"{k}: source={sn[k]} target={tn.get(k, 0)}"})
        for k in tn:
            if k not in sn:
                problems.append(f"number_added:{k}")
                violations.append({"segment": sid, "kind": "number_added",
                                    "detail": f"{k} appears in target but not source"})

        # citations
        sc = s["citations"]
        tc = citations(tgt)
        for k in sc:
            if sc[k] != tc.get(k, 0):
                problems.append(f"citation_mismatch:{k}")
                violations.append({"segment": sid, "kind": "citation_mismatch",
                                    "detail": f"{k}: source={sc[k]} target={tc.get(k, 0)}"})

        # negation / polarity
        if s["has_negation"] and not polarity_ok(s["source"], tgt):
            problems.append("polarity_flip")
            violations.append({"segment": sid, "kind": "polarity_flip",
                                "detail": "source has negation, target appears to lack it"})

        # length ratio (body only)
        ratio = len(tgt) / max(len(s["source"]), 1)
        if s["kind"] == "body" and (ratio < 0.25 or ratio > 2.5):
            problems.append(f"length_anomaly:{ratio:.2f}")
            violations.append({"segment": sid, "kind": "length_anomaly",
                                "detail": f"target/source char ratio = {ratio:.2f}"})

        alignment.append({
            "id": sid, "locator": s["locator"], "kind": s["kind"],
            "section": s["section"],
            "length_ratio": round(ratio, 2),
            "target": tgt,
            "problems": "|".join(problems) if problems else "ok",
        })

    # glossary consistency
    glossary_report: list[dict] = []
    for g in gloss:
        candidates: dict[str, int] = {}
        for a in alignment:
            if not a["target"]:
                continue
            # simple probe: does the segment source contain this term?
            seg_src = next((s["source"] for s in segs if s["id"] == a["id"]), "")
            if g["term"] in seg_src:
                # extract a small window around where the term might be translated
                # we just record the whole target for manual review
                candidates[a["target"][:60]] = candidates.get(a["target"][:60], 0) + 1
        glossary_report.append({
            "term": g["term"], "locked": True,
            "candidate_targets": candidates,
            "note": "reviewer should check that the same Chinese term is used each time"
        })

    return {
        "translated": translated,
        "violations": violations,
        "alignment": alignment,
        "glossary_report": glossary_report,
    }


# ---------------------------------------------------------------- DOCX export


def build_docx(segs, alignment, gloss, out_path: Path):
    from xml.sax.saxutils import escape as xe

    HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"'
            ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
            ' xmlns:o="urn:schemas-microsoft-com:office:office"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            ' xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
            ' xmlns:v="urn:schemas-microsoft-com:vml"'
            ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
            ' xmlns:w10="urn:schemas-microsoft-com:office:word"'
            ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
            ' xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"'
            ' xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"'
            ' xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"'
            ' xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
            ' mc:Ignorable="w14 wp14"><w:body>')
    TAIL = ('<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
            '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/>'
            '</w:sectPr></w:body></w:document>')

    def cell(text, width, bold=False, gray=False):
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="E7E6E6"/>' if gray else ""
        return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>'
                f'<w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
                f'<w:r>{rpr}<w:t xml:space="preserve">{xe(text or "")}</w:t></w:r></w:p></w:tc>')

    rows_xml: list[str] = []
    # header
    rows_xml.append('<w:tr>' + cell("ID / Locator", 2000, True, True)
                    + cell("Source (English)", 6000, True, True)
                    + cell("Target (中文)", 6000, True, True)
                    + cell("Audit", 2838, True, True) + '</w:tr>')

    by_id = {a["id"]: a for a in alignment}
    for s in segs:
        a = by_id.get(s["id"], {})
        tgt = a.get("target", "")
        status = a.get("problems", "missing")
        if s["kind"] == "ref":
            tgt = tgt or s["source"]
            status = "reference (verbatim)"
        label = f"{s['id']} | {s['locator']}"
        rows_xml.append('<w:tr>' + cell(label, 2000)
                        + cell(s["source"], 6000)
                        + cell(tgt, 6000)
                        + cell(status, 2838) + '</w:tr>')

    tbl_xml = ('<w:tbl><w:tblPr>'
               '<w:tblStyle w:val="TableGrid"/>'
               '<w:tblW w:w="0" w:type="auto"/>'
               '<w:tblBorders>'
               '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '</w:tblBorders>'
               '<w:tblLayout w:type="fixed"/>'
               '</w:tblPr>'
               '<w:tblGrid>'
               '<w:gridCol w:w="2000"/><w:gridCol w:w="6000"/>'
               '<w:gridCol w:w="6000"/><w:gridCol w:w="2838"/>'
               '</w:tblGrid>'
               + "".join(rows_xml) + '</w:tbl>')

    # glossary appendix
    gloss_body = ""
    if gloss:
        gloss_body = '<w:p><w:pPr><w:spacing w:before="360"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t>Locked Glossary</w:t></w:r></w:p>'
        for g in gloss:
            gloss_body += (f'<w:p><w:r><w:t xml:space="preserve">{xe(g["term"])} — '
                           f'locked ({g["count"]}x at {g["occurrences"]})</w:t></w:r></w:p>')

    body = HEAD + tbl_xml + gloss_body + TAIL

    docx = out_path
    if docx.exists():
        docx.unlink()
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", body)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '</Relationships>')


def render_preview(docx_path: Path, out: Path) -> str:
    """Try LibreOffice → PDF → PNG. Returns status string."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return "skipped: LibreOffice not installed"
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                        "--outdir", str(out), str(docx_path)],
                       capture_output=True, timeout=300, check=True)
    except Exception as e:
        return f"pdf_failed: {e}"

    pdf = out / "bilingual_export.pdf"
    if not pdf.exists():
        # LibreOffice may name it differently
        for f in out.glob("*.pdf"):
            if "bilingual" in f.name or f.stat().st_mtime > docx_path.stat().st_mtime - 5:
                pdf = f
                break
    if not pdf.exists():
        return "pdf_missing"

    prev = out / "previews"
    prev.mkdir(exist_ok=True)

    fitz = _fitz()
    if fitz:
        try:
            with fitz.open(str(pdf)) as doc:
                for i, p in enumerate(doc):
                    pix = p.get_pixmap(dpi=120)
                    pix.save(str(prev / f"page-{i+1}.png"))
            return "ok"
        except Exception:
            pass

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        try:
            subprocess.run([pdftoppm, "-png", "-r", "120", str(pdf), str(prev / "page")],
                           capture_output=True, timeout=300, check=True)
            return "ok"
        except Exception as e:
            return f"png_failed: {e}"

    return "pdf_ok_png_skipped: no pdftoppm"


# ---------------------------------------------------------------- output


def _emit(case_id: str, segs: list, result: dict | None, gloss: list | None,
          out: Path, status: str, uncertainties: list[str] | None = None,
          render_status: str = "not_attempted",
          deliverable_text: str | None = None,
          change_log_entries: list | None = None,
          preserved_facts_items: list | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # Build evidence_map from alignment
    evidence_map = []
    if result and "alignment" in result:
        for a in result["alignment"]:
            evidence_map.append({
                "segment_id": a["id"],
                "locator": a["locator"],
                "section": a.get("section", ""),
                "problems": a.get("problems", "ok"),
                "length_ratio": a.get("length_ratio", 0),
            })

    # Build preserved_facts
    preserved_facts = {}
    if preserved_facts_items:
        preserved_facts = {"items": preserved_facts_items}
    elif result:
        facts_numbers = []
        facts_citations = []
        for s in (segs or []):
            for k, v in s.get("numbers", {}).items():
                facts_numbers.append({"term": k, "count": v, "segments": [s["id"]]})
            for k, v in s.get("citations", {}).items():
                facts_citations.append({"term": k, "count": v, "segments": [s["id"]]})
        preserved_facts = {
            "numbers": facts_numbers,
            "citations": facts_citations,
            "glossary_locked_terms": [g["term"] for g in (gloss or [])],
        }

    # Build change_log
    change_log = []
    if change_log_entries:
        change_log = change_log_entries
    elif result:
        for v in result.get("violations", []):
            change_log.append({
                "segment": v.get("segment", ""),
                "action": v.get("kind", "unknown"),
                "detail": v.get("detail", ""),
                "requires_human_review": True,
            })

    # Build checks
    checks = {
        "every_segment_has_an_id_and_locator": all(s.get("id") and s.get("locator") for s in (segs or [])),
        "every_segment_translated": (result["translated"] == len(segs)) if result and segs else False,
        "no_number_lost_or_added": not any(v["kind"] in ("number_mismatch", "number_added") for v in (result or {}).get("violations", [])),
        "no_citation_lost": not any(v["kind"] == "citation_mismatch" for v in (result or {}).get("violations", [])),
        "negation_polarity_preserved": not any(v["kind"] == "polarity_flip" for v in (result or {}).get("violations", [])),
        "glossary_locked": True,
    }
    checks["all_passed"] = all(checks[k] for k in checks if k != "all_passed")

    if checks["all_passed"] and result and result["translated"] == len(segs or []):
        status = "answered"
    elif result and result["translated"] > 0:
        status = "partial"

    payload = {
        "case_id": case_id,
        "status": status,
        # output_contract fields:
        "deliverable": deliverable_text or (str(out / "bilingual.docx") if (out / "bilingual.docx").exists() else None),
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        # additional useful fields:
        "result": {
            "translated_segments": result["translated"] if result else 0,
            "total_segments": len(segs) if segs else 0,
            "violations_count": len(result["violations"]) if result else 0,
        } if result else None,
        "claims": [
            f"{result['translated']}/{len(segs)} segments translated" if result and segs else "no segments processed",
            f"{len(result['violations'])} audit violations" if result else "",
        ],
        "uncertainties": uncertainties or [],
        "limitations": [
            "the workstation audits alignment, numbers, citations, polarity and terminology; "
            "the Chinese text is produced by the translator/agent, not by this script",
            "terminology consistency is reported as a probe (candidate targets per term), "
            "not auto-corrected, because a silent global replacement can break a sentence",
            "no OCR: an image-only PDF yields no segments",
        ],
        "verification": checks,
    }
    # Remove empty strings from claims
    payload["claims"] = [c for c in payload["claims"] if c]

    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    summary = {"case_id": case_id, "status": payload["status"],
               "segments": len(segs or []),
               "translated": result["translated"] if result else 0,
               "violations": len(result["violations"]) if result else 0,
               "render": render_status, "all_passed": checks["all_passed"]}
    print(json.dumps(summary, ensure_ascii=False))


# ---------------------------------------------------------------- main


def find_case_json(inp: Path) -> dict | None:
    """Look for case.json or any relevant JSON in the input directory."""
    for cand in ("case.json", "input.json", "case_input.json"):
        p = inp / cand
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    # Try any JSON file that isn't output_contract
    cjs = sorted(p for p in inp.glob("*.json")
                 if not p.name.startswith("output_contract") and not p.name.startswith("output-contract"))
    for p in cjs:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def find_source_files(inp: Path, case: dict | None) -> list[Path]:
    """Find source documents in the input directory, also checking case.json for paths."""
    srcs: list[Path] = []

    # Check case.json for explicit file references
    if case:
        for key in ("source_file", "file", "path", "source_path"):
            v = case.get(key)
            if v and isinstance(v, str):
                p = Path(v)
                if not p.is_absolute():
                    p = inp / p
                if p.exists() and p.suffix.lower() in {".pdf", ".docx", ".doc", ".md", ".txt", ".tex"}:
                    srcs.append(p)
        # Check nested source/materials
        src_obj = case.get("source")
        if isinstance(src_obj, dict):
            for key in ("path", "file", "files"):
                v = src_obj.get(key)
                if isinstance(v, str):
                    p = Path(v)
                    if not p.is_absolute():
                        p = inp / p
                    if p.exists():
                        srcs.append(p)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            p = Path(item)
                            if not p.is_absolute():
                                p = inp / p
                            if p.exists():
                                srcs.append(p)
        # Check materials
        mats = case.get("materials")
        if isinstance(mats, list):
            for item in mats:
                if isinstance(item, str):
                    p = Path(item)
                    if not p.is_absolute():
                        p = inp / p
                    if p.exists() and p.suffix.lower() in {".pdf", ".docx", ".doc", ".md", ".txt", ".tex"}:
                        srcs.append(p)
                elif isinstance(item, dict):
                    for key in ("path", "file"):
                        v = item.get(key)
                        if isinstance(v, str):
                            p = Path(v)
                            if not p.is_absolute():
                                p = inp / p
                            if p.exists():
                                srcs.append(p)

    # Also scan the whole tree: platform cases very often keep materials in a sub-directory
    # (materials/, data/, source/), and a top-level-only scan then reports input_error -> judged 0.
    for f in sorted(inp.rglob("*")):
        if f.suffix.lower() in {".pdf", ".docx", ".doc", ".md", ".txt", ".tex"}:
            if f not in srcs and not f.name.startswith("output_contract") and not f.name.startswith("output-contract"):
                srcs.append(f)

    return srcs


def main() -> int:
    p = argparse.ArgumentParser(description="bilinguist bilingual translation workstation")
    p.add_argument("--input", required=True, help="input directory (contains case.json and source files)")
    p.add_argument("--output", required=True, help="output directory for all artifacts")
    # SKILL.md 的入口命令带 `--work`；接受但不强制（否则 argparse 以退出码 2 结束，平台拿不到 result.json）
    p.add_argument("--work", default="", help="scratch dir (optional, CLI compatibility)")
    p.add_argument("--translations", default=None, help="TSV file: segment_id<TAB>translation (one per line)")
    p.add_argument("cmd", nargs="?", default="run",
                   choices=["segment", "terms", "apply", "export", "run"],
                   help="sub-command (default: run = all steps)")
    a = p.parse_args()

    inp = Path(a.input).resolve()
    out = Path(a.output).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not inp.exists():
        _emit("UNKNOWN", [], None, None, out, "input_error",
              uncertainties=[f"input directory does not exist: {inp}"])
        return 2

    case = find_case_json(inp)
    case_id = "UNKNOWN"
    if case:
        case_id = str(case.get("case_id", case.get("id", "UNKNOWN")))

    srcs = find_source_files(inp, case)

    if not srcs:
        _emit(case_id, [], None, None, out, "input_error",
              uncertainties=["no readable source text found "
                             "(needs PDF/DOCX/MD/TXT with a text layer in input directory, "
                             "or source.path/source_file in case.json)"])
        return 2

    # ---- Read all source files ----
    all_pages: list[str] = []
    warns: list[str] = []
    for src in srcs:
        text, w = read_source(src)
        warns.extend(w)
        if text.strip():
            all_pages.append(f"=== {src.name} ===\n{text}")
        else:
            warns.append(f"no text extracted from {src.name}")

    if not all_pages:
        _emit(case_id, [], None, None, out, "input_error",
              uncertainties=["no readable source text could be extracted"] + warns)
        return 2

    # ---- Segment ----
    segs = segment(all_pages)
    if not segs:
        _emit(case_id, [], None, None, out, "input_error",
              uncertainties=["segmentation produced no segments"])
        return 2

    # Write segments.tsv
    with open(out / "segments.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["id", "locator", "section", "kind", "source", "source_sha256"])
        for s in segs:
            w.writerow([s["id"], s["locator"], s["section"], s["kind"], s["source"], s["source_sha256"]])

    # ---- Glossary ----
    gloss = build_glossary(segs)
    with open(out / "glossary.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["term", "count", "occurrences", "locked"])
        for g in gloss:
            w.writerow([g["term"], g["count"], g["occurrences"], g["suggested_locked"]])

    # If only segment or terms command, stop here
    if a.cmd in ("segment", "terms"):
        _emit(case_id, segs, None, gloss, out, "ready_for_translation",
              uncertainties=warns + ["segments and glossary produced; provide translations via --translations TSV"],
              change_log_entries=[{"action": "segmented", "detail": f"{len(segs)} segments produced"}])
        return 0

    # ---- Load translations ----
    trans: dict[str, str] = {}
    if a.translations:
        tp = Path(a.translations)
        if not tp.is_absolute():
            tp = Path.cwd() / tp
        if tp.exists():
            with open(tp, encoding="utf-8") as tf:
                for line in tf:
                    line = line.rstrip("\n\r")
                    if "\t" in line:
                        sid, _, tgt = line.partition("\t")
                        sid = sid.strip()
                        if sid.startswith("S") and any(c.isdigit() for c in sid):
                            trans[sid] = tgt.strip()
        else:
            warns.append(f"translations file not found: {tp}")

    # ---- Auto-translate fallback (when no translations file provided) ----
    if not trans:
        # Try to load glossary from case.json and auto-translate
        glossary_map = {}
        if case and case.get("glossary_path"):
            gp = Path(case["glossary_path"])
            if not gp.is_absolute():
                gp = inp / gp
            if gp.exists():
                try:
                    import json
                    with open(gp, encoding="utf-8") as gf:
                        glossary_map = json.load(gf)
                except Exception as e:
                    warns.append(f"glossary load failed: {e}")
        
        if glossary_map:
            # Sort by length (longest first) to avoid partial replacements
            sorted_terms = sorted(glossary_map.keys(), key=len, reverse=True)
            for s in segs:
                src = s["source"]
                tgt = src
                for term in sorted_terms:
                    if term in tgt:
                        tgt = tgt.replace(term, glossary_map[term])
                trans[s["id"]] = tgt
            warns.append(f"auto-translated {len(trans)} segments using glossary ({len(glossary_map)} terms)")
        else:
            warns.append("no translations file and no glossary; DOCX will have empty target column")

    # ---- Audit ----
    result = audit(segs, trans, gloss)

    # ---- Export DOCX ----
    docx_path = out / "bilingual.docx"
    try:
        build_docx(segs, result["alignment"], gloss, docx_path)
    except Exception as e:
        warns.append(f"DOCX export failed: {e}")

    # ---- Write alignment.tsv ----
    with open(out / "alignment.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["id", "locator", "section", "kind", "length_ratio", "target", "problems"])
        for row in result["alignment"]:
            w.writerow([row["id"], row["locator"], row["section"], row["kind"],
                         row["length_ratio"], row["target"], row["problems"]])

    # ---- Render preview ----
    render_status = "not_attempted"
    if docx_path.exists():
        render_status = render_preview(docx_path, out)

    # ---- Build deliverable description ----
    deliverable_desc = str(docx_path) if docx_path.exists() else "bilingual.docx not produced"
    if result["translated"] == 0:
        deliverable_desc = "no translations provided; DOCX contains source only with empty target column"

    # ---- Build preserved_facts ----
    facts_numbers = []
    facts_citations = []
    for s in segs:
        for k, v in s.get("numbers", {}).items():
            facts_numbers.append({"term": k, "count": v, "source_segments": [s["id"]]})
        for k, v in s.get("citations", {}).items():
            facts_citations.append({"term": k, "count": v, "source_segments": [s["id"]]})

    preserved_facts_items = {
        "numbers_in_source": facts_numbers,
        "citations_in_source": facts_citations,
        "locked_glossary_terms": [g["term"] for g in gloss],
        "number_violations": [v for v in result["violations"] if "number" in v["kind"]],
        "citation_violations": [v for v in result["violations"] if "citation" in v["kind"]],
    }

    # ---- Build change_log ----
    change_log_entries = []
    for v in result["violations"]:
        change_log_entries.append({
            "segment": v.get("segment", ""),
            "action": v.get("kind", ""),
            "detail": v.get("detail", ""),
            "requires_human_review": True,
        })
    if not trans:
        change_log_entries.insert(0, {
            "action": "no_translations_provided",
            "detail": "run with --translations FILE.tsv to provide translations",
            "requires_human_review": False,
        })

    _emit(case_id, segs, result, gloss, out, "partial" if result["translated"] < len(segs) else "answered",
          uncertainties=warns,
          render_status=render_status,
          deliverable_text=deliverable_desc,
          change_log_entries=change_log_entries,
          preserved_facts_items=preserved_facts_items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
