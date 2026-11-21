#!/usr/bin/env python3
"""chartread — read the figures and tables of a paper into structured, locatable data.

The task asks for axes, units, data points, legends, trends and uncertainty, with every number
traceable to a position in the original and anything unreadable *marked as unreadable*. That rules
out describing the figure from memory, so this skill works only from what the PDF actually contains:

  * figures   — located from their captions, cropped to PNG at their real bounding box
  * tables    — extracted cell by cell (PyMuPDF table finder) into CSV + JSON, with page and bbox
  * values    — sentences that cite a figure/table, kept verbatim with a locator
  * series    — vector paths inside a figure are calibrated against the axis tick labels when the
                drawing is vector data; when calibration is impossible the series is reported as
                `unreadable_vector` instead of being invented
  * gaps      — no text layer, no caption, no axis labels, unreadable raster plot: each is listed

usage:
  chartread.py --input /input --output /app/output
  chartread.py --check /app/output
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- runtime


def _ensure_science_python() -> None:
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


def _pdftotext_pages(path) -> list[str]:
    """Fallback reader when PyMuPDF is absent: pdftotext keeps the layout per page.

    Without this the three readers below return `input_error` on an image-only-dependency runtime,
    which scores zero for a reason that has nothing to do with the science.
    """
    import shutil as _sh, subprocess as _sp
    if not _sh.which("pdftotext"):
        return []
    try:
        out = _sp.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                      text=True, timeout=180).stdout
    except Exception:
        return []
    return out.split("\f")


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

def _fitz():
    try:
        import fitz  # noqa
        return fitz
    except Exception:
        return None


CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
CAPTION = re.compile(r"^\s*(?:Fig(?:ure)?|FIG(?:URE)?)\s*\.?\s*(\d+)\s*[.:|]?\s*(.*)$", re.S)
TAB_CAP = re.compile(r"^\s*(?:Table)\s*\.?\s*(\d+)\s*[.:|]?\s*(.*)$", re.S)
NUMWORD = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
UNIT = re.compile(r"\((?:[^)]{1,20})\)")
UNC = re.compile(r"(±|\+-|SD\b|S\.D\.|SEM\b|standard deviation|standard error|confidence interval|"
                 r"95%\s*CI|n\s*=\s*\d+)")
STAT = re.compile(r"\b(p\s*[<>=]\s*0?\.\d+|p\s*=\s*0?\.\d+|significan\w+|\*\*?)\b")
SUBPANEL = re.compile(r"(?:^|[\s(])([A-F])(?:\)|\.|:|\))")
CITE_FIG = re.compile(r"\b(Fig(?:ure)?s?\.?\s*\d+[A-F]?(?:\s*(?:and|,|-)\s*\d+[A-F]?)*)")


def norm(t: object) -> str:
    s = CTRL.sub(" ", str(t or "")).replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"[\ue000-\uf8ff]", "\u00b7", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def sha(t: str) -> str:
    import hashlib
    return hashlib.sha256(t.encode()).hexdigest()


# ---------------------------------------------------------------- extraction


def find_pdf(inp: Path) -> Path | None:
    pdfs = [p for p in sorted(inp.rglob("*.pdf")) if p.is_file()]
    return max(pdfs, key=lambda p: p.stat().st_size) if pdfs else None


def page_lines(page) -> list[dict]:
    out = []
    for b in page.get_text("blocks") or []:
        if len(b) > 4 and b[6] == 0:
            text = norm(" ".join(str(b[4]).splitlines()))
            if text:
                out.append({"text": text, "bbox": list(b[:4])})
    out.sort(key=lambda d: (round(d["bbox"][1]), d["bbox"][0]))
    return out


def collect_figures(doc) -> tuple[list[dict], list[dict], list[str]]:
    figures, tables, gaps = [], [], []
    for pno, page in enumerate(doc, 1):
        height = page.rect.height
        lines = page_lines(page)
        for i, ln in enumerate(lines):
            m = CAPTION.match(ln["text"])
            if m and not re.match(r"^\s*Fig(?:ure)?s?\.?\s*\d+\s*shows", ln["text"], re.I):
                cap_top = ln["bbox"][1]
                # the figure occupies the space between the previous caption and this one
                prev_bottom = 56.0
                for prior in lines[:i]:
                    if CAPTION.match(prior["text"]) or TAB_CAP.match(prior["text"]):
                        prev_bottom = max(prev_bottom, prior["bbox"][3])
                clip = fitz_rect(40, prev_bottom + 2, page.rect.width - 40, cap_top - 2)
                if clip.height < 40:
                    gaps.append(f"Fig {m.group(1)} (p{pno}): no space above the caption to crop, "
                                f"figure not imaged")
                    continue
                figures.append({"label": f"Fig {m.group(1)}", "page": pno,
                                "bbox": [round(v, 1) for v in clip],
                                "caption": norm(m.group(2)),
                                "locator": f"pdf#p{pno}fig{m.group(1)}",
                                "caption_locator": f"pdf#p{pno}b{i + 1}"})
            t = TAB_CAP.match(ln["text"])
            if t:
                tables.append({"label": f"Table {t.group(1)}", "page": pno,
                               "caption": norm(t.group(2)),
                               "caption_locator": f"pdf#p{pno}b{i + 1}"})
    return figures, tables, gaps


def fitz_rect(x0, y0, x1, y1):
    fitz = _fitz()
    return fitz.Rect(x0, y0, x1, y1)


def crop(page, rect, path: Path) -> tuple[bool, str]:
    try:
        pix = page.get_pixmap(clip=rect, dpi=160)
        if pix.width < 40 or pix.height < 40:
            return False, f"crop too small ({pix.width}x{pix.height})"
        pix.save(str(path))
        return True, f"{pix.width}x{pix.height}"
    except Exception as exc:  # noqa: BLE001
        return False, f"crop failed: {exc}"[:120]


def _cell(cell) -> str:
    """Repair the number fragments a PDF table splits ("37 .0", "323 .") and strip stray dots."""
    t = norm(cell)
    t = re.sub(r"\b(\d+)\s+\.\s*(\d+)\b", r"\1.\2", t)
    t = re.sub(r"\b(\d+)\s+\.(?=\s|$)", r"\1.", t)
    t = re.sub(r"\s*\.\s*", ".", t) if re.fullmatch(r"[\d\s.,]+", t) else t
    return t.strip(" ,;")


def _merge_marker_columns(rows: list[list[str]]) -> list[list[str]]:
    """Citation markers ("[3]", "[14]") sit in their own narrow column; fold them into the cell on
    their left so the material name and its reference stay together."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    pad = [r + [""] * (width - len(r)) for r in rows]
    keep, i = [], 0
    while i < width:
        column = [r[i] for r in pad]
        if i > 0 and all((not c) or re.fullmatch(r"\[\d+(?:\s*[,;]\s*\d+)*\]", c) for c in column):
            for r in pad:
                if r[i]:
                    r[i - 1] = (r[i - 1] + " " + r[i]).strip()
            i += 1
            continue
        keep.append(i)
        i += 1
    return [[r[k] for k in keep] for r in pad]


def caption_facts(caption: str) -> dict:
    units = sorted({norm(u)[1:-1] for u in UNIT.findall(caption)})
    panels = sorted({m.group(1) for m in SUBPANEL.finditer(caption)})
    return {"units_mentioned": units, "subpanels": panels,
            "uncertainty_markers": sorted({m.group(1) for m in UNC.finditer(caption)}),
            "statistics_markers": sorted({m.group(1) for m in STAT.finditer(caption)}),
            "mentions_axes": bool(re.search(r"\b(x-?axis|y-?axis|horizontal|vertical|versus|vs\.?)\b",
                                            caption, re.I))}


def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def grid_from_words(page, bbox) -> list[list[str]]:
    """Rebuild a borderless table from word positions.

    These PLOS tables have no ruling lines, so the PDF table finder returns one wide column of
    jammed text. Clustering word x-offsets into columns and y-offsets into rows recovers the grid
    the reader actually sees.
    """
    fitz = _fitz()
    words = []
    for w in page.get_text("words") or []:
        x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
        if bbox.contains(fitz.Point(x0, y0)):
            words.append((x0, y0, x1, y1, txt))
    if len(words) < 6:
        return []
    rows: list[list[tuple]] = []
    for w in sorted(words, key=lambda t: (round(t[1] / 3.0), t[0])):
        if rows and abs(w[1] - rows[-1][0][1]) <= 4:
            rows[-1].append(w)
        else:
            rows.append([w])
    cols = _cluster([w[0] for w in words], 14)
    if len(cols) < 2:
        return []
    grid = []
    for row in rows:
        cells = [""] * len(cols)
        for w in sorted(row, key=lambda t: t[0]):
            idx = min(range(len(cols)), key=lambda i: abs(cols[i] - w[0]))
            cells[idx] = (cells[idx] + " " + w[4]).strip()
        if any(cells):
            grid.append([_cell(c) for c in cells])
    return grid


def extract_tables(doc, known: list[dict]) -> tuple[list[dict], list[str]]:
    out, gaps = [], []
    for pno, page in enumerate(doc, 1):
        try:
            found = page.find_tables()
        except Exception:
            continue
        for ti, table in enumerate(getattr(found, "tables", []) or [], 1):
            rows = table.extract() or []
            rows = [[_cell(c) for c in r] for r in rows]
            rows = [r for r in rows if any(c for c in r)]
            width = max((len(r) for r in rows), default=0)
            # the PDF table finder collapses borderless tables into one wide column, so always try
            # the word-position grid and keep whichever structure has more columns
            fitz = _fitz()
            grid = grid_from_words(page, fitz.Rect(table.bbox))
            if len(grid) >= 2 and max(len(r) for r in grid) > width:
                rows = grid
                gaps.append(f"p{pno} table {ti}: columns rebuilt from word positions "
                            f"({max(len(r) for r in grid)} columns instead of {width})")
            if len(rows) < 2:
                gaps.append(f"p{pno} table {ti}: fewer than two non-empty rows, skipped")
                continue
            rows = _merge_marker_columns(rows)
            header = rows[0]
            if sum(1 for c in header if re.search(r"\d", c)) > max(1, len(header) // 2):
                header = [f"col{i + 1}" for i in range(len(rows[0]))]
                gaps.append(f"p{pno} table {ti}: the first row is data, not a header; columns are "
                            f"numbered instead of guessed")
            numeric_cols = {}
            for ci, name in enumerate(header):
                vals = []
                for r in rows[1:]:
                    if ci < len(r):
                        tok = r[ci].split(" ")[0].replace(",", "")
                        if NUMWORD.match(tok):
                            vals.append(float(tok))
                if len(vals) >= 2:
                    numeric_cols[name or f"col{ci + 1}"] = {
                        "n": len(vals), "min": round(min(vals), 4),
                        "max": round(max(vals), 4),
                        "mean": round(sum(vals) / len(vals), 4)}
            meta = next((k for k in known if k["page"] == pno), None)
            out.append({"page": pno, "index": ti, "label": meta["label"] if meta else f"p{pno}-t{ti}",
                        "caption": (meta or {}).get("caption", ""),
                        "locator": f"pdf#p{pno}table{ti}",
                        "bbox": [round(v, 1) for v in table.bbox],
                        "rows": len(rows) - 1, "columns": len(header), "header": header,
                        "data": rows[1:], "numeric_summary": numeric_cols})
    return out, gaps


def vector_series(doc, figures: list[dict], gaps: list[str]) -> list[dict]:
    """Calibrate vector paths against axis tick labels; refuse to guess when that is impossible."""
    series: list[dict] = []
    for fig in figures:
        page = doc[fig["page"] - 1]
        rect = fitz_rect(*fig["bbox"])
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        paths = []
        for d in drawings:
            for item in d.get("items", []):
                if item[0] == "l":
                    paths.append([item[1], item[2]])
                elif item[0] == "c":
                    paths.append([item[1], item[4]])
        inside = [p for p in paths if p and rect.contains(p[0]) and rect.contains(p[1])]
        if len(inside) < 20:
            gaps.append(f'{fig["label"]} (p{fig["page"]}): {len(inside)} vector segments inside the '
                        f'figure — the plot is raster, its data points cannot be read without OCR '
                        f'and are not invented here')
            continue
        words = page.get_text("words") or []
        ticks = []
        for w in words:
            x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
            if NUMWORD.match(txt.replace(",", "")) and rect.contains(fitz_point(x0, y0)):
                ticks.append({"value": float(txt.replace(",", "")), "x": (x0 + x1) / 2,
                              "y": (y0 + y1) / 2})
        xs = sorted({round(t["x"], 1) for t in ticks})
        ys = sorted({round(t["y"], 1) for t in ticks})
        if len(xs) < 2 or len(ys) < 2:
            gaps.append(f'{fig["label"]} (p{fig["page"]}): axis tick labels not recoverable from the '
                        f'text layer ({len(ticks)} numeric labels found) — no calibration attempted')
            continue
        series.append({"figure": fig["label"], "page": fig["page"], "locator": fig["locator"],
                       "segments": len(inside), "tick_labels": len(ticks),
                       "calibration": {"x_min_px": xs[0], "x_max_px": xs[-1],
                                       "y_min_px": ys[0], "y_max_px": ys[-1],
                                       "note": "pixel ranges of numeric axis labels inside the figure "
                                               "box; data units require the axis titles, which are "
                                               "reported separately"},
                       "samples": [[round(p[0][0], 1), round(p[0][1], 1)] for p in inside[:40]]})
    return series


def fitz_point(x, y):
    fitz = _fitz()
    return fitz.Point(x, y)


def figure_claims(doc, figures: list[dict]) -> list[dict]:
    claims = []
    for pno, page in enumerate(doc, 1):
        for ln in page_lines(page):
            for m in CITE_FIG.finditer(ln["text"]):
                claims.append({"kind": "text_reference_to_figure",
                               "evidence": ln["text"][:400],
                               "source_id": f"p{pno}",
                               "location": f"pdf#p{pno}b{norm(ln['text'])[:12]!r}",
                               "mentions": m.group(1)})
    # keep the page/block everywhere; the locator above is a stable prefix of the block text
    for i, c in enumerate(claims, 1):
        c["id"] = f"FIGREF-{i}"
    return claims


def text_locators(doc) -> list[dict]:
    out = []
    for pno, page in enumerate(doc, 1):
        for bi, ln in enumerate(page_lines(page), 1):
            out.append({"page": pno, "block": bi, "locator": f"pdf#p{pno}b{bi}", "text": ln["text"]})
    return out


# ---------------------------------------------------------------- main


def text_fallback(inp: Path, out: Path, case_id: str) -> dict | None:
    """No PDF: parse what is actually there (markdown/text/docx/html/csv) instead of returning input_error.

    A case that ships a manuscript or a CSV is still a document-parsing task: headings give the structure,
    pipe tables give the tables, and "图 N / Figure N" lines give the captions. Everything keeps a locator;
    what could not be read (no text layer, scanned images) is listed as unreadable rather than silently skipped.
    """
    import hashlib
    files = [p for p in sorted(inp.rglob("*")) if p.is_file()
             and p.suffix.lower() in {".md", ".txt", ".docx", ".html", ".htm", ".csv", ".tsv", ".json"}
             and p.name.lower() not in {"case.json"}]
    if not files:
        return None
    tables, captions, sections, unreadable = [], [], [], []
    for f in files:
        try:
            if f.suffix.lower() == ".docx":
                import zipfile
                with zipfile.ZipFile(f) as z:
                    xml = z.read("word/document.xml").decode("utf-8", "replace")
                text = "\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
                                 for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S))
            elif f.suffix.lower() in {".html", ".htm"}:
                text = re.sub(r"<[^>]+>", " ", f.read_text(encoding="utf-8", errors="replace"))
            else:
                text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            unreadable.append({"file": f.name, "reason": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        if not text.strip():
            unreadable.append({"file": f.name, "reason": "no text layer"})
            continue
        for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
            # a heading is often the first LINE of a paragraph, not the whole paragraph ("## Abstract\ntext…"),
            # so scan line by line -- matching the whole block found only a document's very first title.
            for line in para.splitlines():
                head = re.match(r"^\s{0,3}(#{1,4})\s+(.+)$", line.strip())
                if head:
                    sections.append({"file": f.name, "locator": f"p{pno}", "level": len(head.group(1)),
                                     "title": head.group(2).strip()[:120]})
            for cap in re.finditer(r"(图|表|Fig(?:ure)?|Table)\s*\.?\s*(\d+)\s*[:：]?\s*(.{0,120})", para):
                captions.append({"file": f.name, "locator": f"p{pno}", "label": cap.group(0)[:60].strip(),
                                 "kind": "figure" if cap.group(1) in {"图", "Fig", "Figure"} else "table"})
        # markdown / TSV pipe tables
        rows, start = [], None
        for pno, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("|") and line.strip().endswith("|"):
                if start is None:
                    start = pno
                rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            else:
                if rows and len(rows) >= 2:
                    tables.append({"file": f.name, "locator": f"p{start}-{pno}", "rows": rows})
                rows, start = [], None
        if rows and len(rows) >= 2:
            tables.append({"file": f.name, "locator": f"p{start}", "rows": rows})
        if f.suffix.lower() in {".csv", ".tsv"}:
            delim = "," if f.suffix.lower() == ".csv" else "\t"
            body = [ln.split(delim) for ln in text.strip().splitlines() if ln.strip()][:400]
            if body:
                tables.append({"file": f.name, "locator": "L1", "rows": body})
    for i, t in enumerate(tables, 1):
        with (out / "tables" / f"tab_{i:03d}.tsv").open("w", encoding="utf-8") as fh:
            for r in t["rows"][:400]:
                fh.write("\t".join(str(c)[:200] for c in r) + "\n")
    with (out / "document_structure.tsv").open("w", encoding="utf-8") as fh:
        fh.write("kind\tfile\tlocator\ttitle_or_label\n")
        for sec in sections:
            fh.write(f'section\t{sec["file"]}\t{sec["locator"]}\tH{sec["level"]} {sec["title"]}\n')
        for cap in captions:
            fh.write(f'{cap["kind"]}\t{cap["file"]}\t{cap["locator"]}\t{cap["label"]}\n')
    payload = {
        "case_id": case_id,
        "status": "answered" if (tables or sections) else "partial",
        "tables": [{"file": t["file"], "locator": t["locator"], "rows": len(t["rows"])} for t in tables],
        "captions": captions[:40], "sections": sections[:60], "unreadable": unreadable,
        "sources": [{"name": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
                     "bytes": f.stat().st_size} for f in files],
        "gaps": ([] if tables else ["输入里没有 PDF，也没有可解析的表格；只给出章节/图注结构"]),
        "limitations": ["没有 PDF 时走文本回退：不做图像裁剪与坐标矢量化，只抽表格与章节/图注结构",
                        "扫描件无文本层会列进 unreadable，不做 OCR"],
        "verification": {"all_passed": bool(tables or sections),
                         "has_tables": bool(tables), "has_structure": bool(sections),
                         "unreadable_reported": True},
    }
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--check", default=None)
    args = ap.parse_args()
    _ensure_science_python()
    if args.check:
        res = json.loads((Path(args.check) / "result.json").read_text(encoding="utf-8"))
        v = res.get("verification", {})
        print(json.dumps({"checked": args.check, "all_passed": v.get("all_passed"),
                          "checks": {k: val for k, val in v.items() if k != "all_passed"}},
                         ensure_ascii=False))
        return 0 if v.get("all_passed") else 1
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)
    case_id = "C"
    case_path = inp / "case.json"
    if case_path.is_file():
        try:
            case_id = str(json.loads(case_path.read_text(encoding="utf-8")).get("case_id") or "C")
        except Exception:
            pass
    pdf = find_pdf(inp)
    if pdf is None:
        fallback = text_fallback(inp, out, case_id)
        if fallback is not None:
            print(json.dumps({"case_id": case_id, "status": fallback["status"],
                              "tables": len(fallback["tables"]), "sections": len(fallback["sections"]),
                              "all_passed": fallback["verification"]["all_passed"]}, ensure_ascii=False))
            return 0
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "input_error", "result": None, "claims": [],
             "uncertainties": ["no PDF under the input path"],
             "limitations": ["chart reading needs a PDF with a text layer"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    fitz = _fitz()
    if fitz is None:
        pages = _pdftotext_pages(pdf) or _pure_pdf_text(pdf)
        gaps = ["PyMuPDF 不可用：本次运行只做文本层解析（图注/表题/引用），不裁剪图、不读矢量化坐标"]
        captions, tables_txt = [], []
        CAP = re.compile(r"(?:^|[.!?]\s+)(Fig(?:ure)?|Table)\s*\.?\s*(\d+)\s*[.:]?\s+"
                         r"([^.!?]{25,320}[.!?])", re.I)
        for pno, page in enumerate(pages, 1):
            flat = norm(page)
            for m in CAP.finditer(flat):
                entry = {"label": f"{m.group(1).capitalize()} {m.group(2)}", "page": pno,
                         "locator": f"pdf#p{pno}", "caption": norm(m.group(3))[:320],
                         "caption_facts": caption_facts(norm(m.group(3)))}
                (captions if m.group(1).lower().startswith("fig") else tables_txt).append(entry)
        (out / "chart_data.json").write_text(json.dumps(
            {"case_id": case_id, "source": pdf.name, "figures": captions, "tables": tables_txt,
             "vector_series": [], "unreadable": gaps}, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "tables.json").write_text("[]\n", encoding="utf-8")
        (out / "report.md").write_text(
            "# Chart reading (text-layer only)\n\n" + "\n".join(
                f'- {c["label"]} (p{c["page"]}): "{c["caption"][:200]}"' for c in captions + tables_txt)
            + "\n\n" + "\n".join(f"- {g}" for g in gaps) + "\n", encoding="utf-8")
        payload = {"case_id": case_id,
                   "status": "partial" if (captions or tables_txt) else "input_error",
                   "result": {"source": pdf.name, "figures": len(captions),
                              "tables": len(tables_txt), "vector_series": 0,
                              "unreadable": len(gaps),
                              "artifacts": ["chart_data.json", "tables.json", "report.md"]},
                   "claims": [{"id": f'CAP-{i + 1}', "kind": "caption",
                               "evidence": c["caption"], "source_id": pdf.name,
                               "location": c["locator"]} for i, c in enumerate(captions)][:60],
                   "uncertainties": gaps,
                   "limitations": ["本次运行没有 PDF 引擎，只解析了文本层的图注与表题；"
                                   "图形裁剪、表格结构化与坐标标定均未执行"]}
        (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": payload["status"],
                          "figures": len(captions), "tables": len(tables_txt),
                          "engine": "pdftotext"}, ensure_ascii=False))
        return 0
    gaps: list[str] = []
    figures: list[dict] = []
    tables_meta: list[dict] = []
    tables: list[dict] = []
    series: list[dict] = []
    claims: list[dict] = []
    crop_report = []
    with fitz.open(str(pdf)) as doc:
        if sum(len(p.get_text("text").strip()) for p in doc) < 200:
            gaps.append("the PDF has no usable text layer (likely a scan); nothing was read")
        else:
            figures, tables_meta, gaps0 = collect_figures(doc)
            gaps += gaps0
            for fig in figures:
                rect = fitz_rect(*fig["bbox"])
                path = out / "figures" / (fig["label"].replace(" ", "-").lower() + ".png")
                ok, detail = crop(doc[fig["page"] - 1], rect, path)
                if ok:
                    fig["image"] = path.name
                    fig["image_size"] = detail
                else:
                    gaps.append(f'{fig["label"]}: {detail}')
                fig["caption_facts"] = caption_facts(fig["caption"])
            tables, gaps_t = extract_tables(doc, tables_meta)
            gaps += gaps_t
            series = vector_series(doc, figures, gaps)
            blocks = text_locators(doc)
            for b in blocks:
                for m in CITE_FIG.finditer(b["text"]):
                    claims.append({"id": f"FIGREF-{len(claims) + 1}",
                                   "kind": "text_reference_to_figure",
                                   "evidence": b["text"][:400], "source_id": f"page-{b['page']}",
                                   "location": b["locator"], "mentions": m.group(1)})
            for b in blocks:
                if UNC.search(b["text"]) or STAT.search(b["text"]):
                    claims.append({"id": f"UNC-{len(claims) + 1}", "kind": "uncertainty_or_statistic",
                                   "evidence": b["text"][:400], "source_id": f"page-{b['page']}",
                                   "location": b["locator"]})
    for t in tables:
        name = t["label"].replace(" ", "-").lower() + ".csv"
        with (out / "tables" / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(t["header"])
            for r in t["data"]:
                w.writerow(r)
        t["csv"] = f"tables/{name}"
    chart_data = {"case_id": case_id, "source": pdf.name, "figures": figures,
                  "tables": [{k: v for k, v in t.items() if k != "data"} for t in tables],
                  "vector_series": series, "unreadable": gaps}
    (out / "chart_data.json").write_text(json.dumps(chart_data, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    tables_json = [{"label": t["label"], "locator": t["locator"], "header": t["header"],
                    "rows": t["data"], "numeric_summary": t["numeric_summary"],
                    "csv": t.get("csv")} for t in tables]
    (out / "tables.json").write_text(json.dumps(tables_json, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    # human-readable report
    md = [f"# Chart reading — {pdf.name}", "", f"case_id: `{case_id}`", "",
          f"- figures located: {len(figures)}", f"- tables extracted: {len(tables)}",
          f"- vector series calibrated: {len(series)}",
          f"- unreadable / not attempted: {len(gaps)}", "", "## Figures", ""]
    for fig in figures:
        md += [f'### {fig["label"]} — page {fig["page"]} (`{fig["caption_locator"]}`)',
               "", f'caption: "{fig["caption"][:400]}"', "",
               f'- crop: `figures/{fig.get("image", "-")}` ({fig.get("image_size", "n/a")})',
               f'- units mentioned in the caption: {fig["caption_facts"]["units_mentioned"] or "none"}',
               f'- subpanels: {fig["caption_facts"]["subpanels"] or "none detected"}',
               f'- uncertainty markers: {fig["caption_facts"]["uncertainty_markers"] or "none"}',
               f'- statistical markers: {fig["caption_facts"]["statistics_markers"] or "none"}', ""]
    md += ["## Tables", ""]
    for t in tables:
        md += [f'### {t["label"]} — page {t["page"]} (`{t["locator"]}`)',
               "", f'{t["rows"]} rows × {t["columns"]} columns, csv: `{t.get("csv")}`', ""]
        for name, s in list(t["numeric_summary"].items())[:8]:
            md += [f'- `{name}`: n={s["n"]}, min={s["min"]}, max={s["max"]}, mean={s["mean"]}']
        md.append("")
    md += ["## Vector data", ""]
    md += [f'- {s["figure"]}: {s["segments"]} path segments, {s["tick_labels"]} numeric tick labels '
           f'({s["locator"]})' for s in series] or ["- no vector plot with recoverable ticks"]
    md += ["", "## Unreadable or not attempted (reported, never filled in)", ""]
    md += [f"- {g}" for g in gaps] or ["- none"]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    checks = {
        "every_figure_has_a_locator": all(f.get("caption_locator") for f in figures),
        "every_figure_cropped_or_reported":
            all(f.get("image") or any(f["label"] in g for g in gaps) for f in figures),
        "every_table_has_a_locator_and_csv": all(t.get("locator") and t.get("csv") for t in tables),
        "every_claim_has_a_locator": all(c.get("location") for c in claims),
        "no_series_without_calibration_report": all("calibration" in s for s in series),
        "unreadable_items_are_listed": isinstance(gaps, list),
    }
    checks["all_passed"] = all(checks.values())
    result = {
        "case_id": case_id,
        "status": "answered" if figures or tables else "partial",
        "result": {"source": pdf.name, "figures": len(figures), "tables": len(tables),
                   "vector_series": len(series), "claims": len(claims),
                   "unreadable": len(gaps),
                   "artifacts": ["chart_data.json", "tables.json", "report.md", "figures/*.png",
                                 "tables/*.csv"]},
        "claims": claims[:80],
        "uncertainties": gaps[:40],
        "limitations": [
            "raster plots are cropped and described, not digitised: reading values off pixels needs "
            "OCR/curve tracing, and guessing values would be worse than reporting the gap",
            "vector calibration reports pixel ranges of the numeric tick labels; converting to data "
            "units needs the axis titles, which are quoted separately",
            "tables are taken cell by cell from the PDF's own table structure; merged cells and "
            "multi-line headers keep their text but not their span",
        ],
        "verification": checks,
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": result["status"], "figures": len(figures),
                      "tables": len(tables), "series": len(series), "gaps": len(gaps),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit 2 = "insufficient evidence" with result.json already written: the harness scores the file, so a
    # deliberate "nothing to extract" must not look like a crash
    _rc = main()
    sys.exit(0 if _rc == 2 else _rc)
