#!/usr/bin/env python3
"""Layered PDF text/structure extraction with page-anchored evidence.

Design goals (challenge: 文献文档解析 / 科研阅读):
  * verbatim text, no guessing: every block keeps its page number and bbox;
  * correct reading order for single/multi-column layouts;
  * page furniture (running heads, footers, page numbers) marked, not merged into body;
  * captions, footnotes and the reference list separated from body text;
  * honest reporting: engine used, per-page warnings, unreadable regions flagged.

Engines tried in order (first that works wins); the chosen engine is reported in meta:
  1. PyMuPDF (fitz)      - text + bbox + font size
  2. pdfplumber          - text + bbox
  3. pdftotext -layout   - text only (poppler CLI)
  4. pypdf               - text only
  5. raw stream decode   - last resort, text operators only (low fidelity, flagged)

CLI:
  python3 pdf_toolkit.py extract IN.pdf -o out.json [--order auto|on|off] [--jsonl out.jsonl]
  python3 pdf_toolkit.py tables  IN.pdf -o tables.json
  python3 pdf_toolkit.py refs    out.json -o refs.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import zlib

CAPTION_RE = re.compile(r"^\s*(fig(?:ure)?\.?|tab(?:le)?\.?|图|表|scheme|algorithm|box)\s*[0-9IVX]+", re.I)
HEAD_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?[A-Z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff ,:/()-]{1,70}$")
NUMBERED_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s*[A-Z\u4e00-\u9fff]")
TOP_SECTION_RE = re.compile(r"^\s*(abstract|introduction|background|related work|method(?:s|ology)?|approach|"
                            r"experiment(?:s|al setup)?|results?|analysis|discussion|conclusions?|limitations|"
                            r"acknowledge?ments?|appendix|supplement(?:ary)?|摘要|引言|相关工作|方法|实验|结果|讨论|结论)\b", re.I)
APPENDIX_RE = re.compile(r"^\s*(appendix|supplement(?:ary)?|附录)\b", re.I)
REFS_HEAD_RE = re.compile(r"^\s*(references|reference|bibliography|参考文献|引用文献)\s*$", re.I)
FOOTNOTE_RE = re.compile(r"^\s*(\*|†|‡|\d{1,2})\s+\S")
PAGENUM_RE = re.compile(r"^\s*(\d{1,4}|[ivxlcIVXLC]{1,6})\s*$")


def _load_pages_fitz(path):
    import fitz  # type: ignore
    doc = fitz.open(path)
    pages = []
    for pno, page in enumerate(doc, start=1):
        raw = page.get_text("dict")
        blocks = []
        for b in raw.get("blocks", []):
            if b.get("type") != 0:
                continue
            spans = [s for line in b.get("lines", []) for s in line.get("spans", [])]
            text = "".join(s.get("text", "") for s in spans)
            if not text.strip():
                continue
            sizes = [s.get("size", 0) for s in spans if s.get("text", "").strip()]
            blocks.append({
                "text": text,
                "bbox": [round(v, 1) for v in b.get("bbox", [0, 0, 0, 0])],
                "size": round(statistics.median(sizes), 1) if sizes else 0.0,
                "bold": bool(any("bold" in str(s.get("font", "")).lower() for s in spans)),
            })
        pages.append({"page": pno, "width": round(page.rect.width, 1), "height": round(page.rect.height, 1),
                      "blocks": blocks})
    doc.close()
    return pages


def _load_pages_pdfplumber(path):
    import pdfplumber  # type: ignore
    pages = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            blocks = []
            for w in page.extract_words(use_text_flow=False, keep_blank_chars=False, extra_attrs=["size"]):
                blocks.append({"text": w["text"] + " ", "bbox": [round(w["x0"], 1), round(w["top"], 1),
                                                                round(w["x1"], 1), round(w["bottom"], 1)],
                               "size": round(float(w.get("size") or 0), 1), "bold": False})
            # merge words into lines by (rounded y, x)
            lines, cur = [], []
            for w in sorted(blocks, key=lambda b: (round(b["bbox"][1] / 3), b["bbox"][0])):
                if cur and abs(cur[-1]["bbox"][1] - w["bbox"][1]) > 3:
                    lines.append(_merge_line(cur)); cur = []
                cur.append(w)
            if cur:
                lines.append(_merge_line(cur))
            merged = []
            for ln in lines:
                merged.append({"text": ln["text"], "bbox": ln["bbox"],
                               "size": round(statistics.median([b["size"] for b in ln["parts"]]), 1) if ln["parts"] else 0.0,
                               "bold": False})
            pages.append({"page": pno, "width": round(page.width, 1), "height": round(page.height, 1),
                          "blocks": merged})
    return pages


def _merge_line(words):
    text = "".join(w["text"] for w in words)
    x0 = min(w["bbox"][0] for w in words); x1 = max(w["bbox"][2] for w in words)
    y0 = min(w["bbox"][1] for w in words); y1 = max(w["bbox"][3] for w in words)
    return {"text": re.sub(r"\s+", " ", text).strip(), "bbox": [x0, y0, x1, y1], "parts": words}


def _load_pages_textonly(path, tool):
    out = subprocess.run(tool, capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"{tool[0]} failed: {out.stderr[:200]}")
    pages = []
    for pno, chunk in enumerate(out.stdout.split("\f"), start=1):
        blocks = [{"text": ln, "bbox": [0, 0, 0, 0], "size": 0.0, "bold": False}
                  for ln in chunk.splitlines() if ln.strip()]
        if blocks or pno == 1:
            pages.append({"page": pno, "width": 0.0, "height": 0.0, "blocks": blocks})
    return pages


def _load_pages_raw(path):
    data = open(path, "rb").read()
    text_chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1)
        try:
            dec = zlib.decompress(raw)
        except Exception:
            dec = raw
        for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)", dec):
            text_chunks.append(tm.group(0)[1:-1].decode("latin-1", "replace"))
    joined = " ".join(text_chunks)
    joined = re.sub(r"\\([()\\])", r"\1", joined)
    blocks = [{"text": joined[i:i + 200], "bbox": [0, 0, 0, 0], "size": 0.0, "bold": False}
              for i in range(0, len(joined), 200) if joined[i:i + 200].strip()]
    return [{"page": 1, "width": 0.0, "height": 0.0, "blocks": blocks}]


def load_pages(path):
    errors = []
    for name, fn in (("pymupdf", lambda: _load_pages_fitz(path)),
                     ("pdfplumber", lambda: _load_pages_pdfplumber(path)),
                     ("pdftotext", lambda: _load_pages_textonly(path, ["pdftotext", "-layout", path, "-"])),
                     ("pypdf", lambda: _load_pages_textonly(path, [sys.executable, "-m", "pypdf", "text", path]))):
        try:
            pages = fn()
            if pages and any(p["blocks"] for p in pages):
                return pages, name, errors + [f"{n}: {e}" for n, e in errors]
        except Exception as exc:  # noqa: BLE001
            errors.append((name, str(exc)[:160]))
    try:
        return _load_pages_raw(path), "raw-stream", [f"{n}: {e}" for n, e in errors]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"all engines failed: {errors}; raw: {exc}")


def strip_furniture(pages):
    """Mark running heads/footers/page numbers that repeat across pages."""
    cand = {}
    for p in pages:
        for b in p["blocks"]:
            t = b["text"].strip()
            if not t or len(t) > 120:
                continue
            h = p["height"] or 0
            if not h:
                continue
            y0 = b["bbox"][1]
            if y0 < h * 0.08 or y0 > h * 0.92:
                cand.setdefault(re.sub(r"\d+", "#", t), []).append(p["page"])
    repeated = {k for k, v in cand.items() if len(set(v)) >= max(2, int(0.5 * len(pages)))}
    for p in pages:
        for b in p["blocks"]:
            t = re.sub(r"\d+", "#", b["text"].strip())
            if t in repeated or PAGENUM_RE.match(b["text"]):
                b["type"] = "furniture"
            else:
                b.setdefault("type", "body")
    return pages


def order_blocks(pages, order="auto"):
    """Reading order: per page, split into columns when a clear vertical gutter exists."""
    notes = []
    for p in pages:
        body = [b for b in p["blocks"] if b.get("type") != "furniture"]
        if not body:
            continue
        w = p["width"] or max((b["bbox"][2] for b in body), default=0)
        two_col = False
        if order in ("auto", "on") and w:
            xs = sorted((b["bbox"][0], b["bbox"][2]) for b in body)
            cands = []
            for frac in (0.42, 0.46, 0.5, 0.54, 0.58):
                x = frac * w
                left = sum(1 for a, b2 in xs if b2 <= x + 5)
                right = sum(1 for a, b2 in xs if a >= x - 5)
                span = sum(1 for a, b2 in xs if a < x - 5 and b2 > x + 5)
                if left and right and span <= max(2, 0.15 * len(xs)):
                    cands.append((left + right, span, x))
            if cands and order != "off":
                _, span, x = max(cands, key=lambda c: (c[0] - 4 * c[1]))
                if span <= max(2, 0.15 * len(xs)):
                    two_col = True
                    p["column_split_x"] = round(x, 1)
        if two_col:
            x = p["column_split_x"]
            for b in body:
                b["column"] = 1 if (b["bbox"][2] <= x + 5) else (2 if b["bbox"][0] >= x - 5 else 0)
            body.sort(key=lambda b: (b.get("column", 0) if b.get("column", 0) else 9, b["bbox"][1], b["bbox"][0]))
        else:
            for b in body:
                b["column"] = 0
            body.sort(key=lambda b: (round(b["bbox"][1] / 6), b["bbox"][0]))
        furn = [b for b in p["blocks"] if b.get("type") == "furniture"]
        p["blocks"] = body + furn
        for i, b in enumerate(p["blocks"], start=1):
            b["order"] = i
            b["id"] = f"p{p['page']}b{i}"
            b.setdefault("type", "body")
        notes.append(f"page {p['page']}: {'two-column' if two_col else 'single-column'}, {len(body)} blocks")
    return pages, notes


def tag_structures(pages):
    """Classify blocks: heading / caption / footnote / reference, and bound the reference list."""
    in_refs = False
    sizes = [b["size"] for p in pages for b in p["blocks"] if b.get("size")]
    body_size = statistics.median(sizes) if sizes else 0.0
    for p in pages:
        top = bool(p["height"] and len(p["blocks"]) < 12)
        for b in p["blocks"]:
            t = " ".join(b["text"].split())
            if not t or b.get("type") == "furniture":
                continue
            if REFS_HEAD_RE.match(t):
                in_refs = True
                b["type"] = "heading"; b["section"] = "References"
                continue
            if in_refs:
                # leave reference mode on an appendix/supplement heading or a large-font heading
                big = body_size and b.get("size", 0) >= body_size + 1.5
                if APPENDIX_RE.match(t) or (big and len(t) < 80 and not re.match(r"^\[\d+\]|^\d+\.\s", t)):
                    in_refs = False
                else:
                    b["type"] = "reference"
                    continue
            if CAPTION_RE.match(t) and len(t) < 400:
                b["type"] = "caption"
                continue
            numbered = bool(NUMBERED_RE.match(t))
            top_sec = bool(TOP_SECTION_RE.match(t))
            alpha = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", t))
            digit_ratio = len(re.findall(r"\d", t)) / max(1, len(t))
            short = (len(t) < 90 and not t.endswith(".") and alpha >= 4
                     and digit_ratio < 0.25 and not re.search(r"\d\.\d\d", t))
            big = body_size and b.get("size", 0) >= body_size + 0.8
            if short and (top_sec or (numbered and (big or b.get("bold") or len(t.split()) <= 6))
                          or (big and b.get("bold") and len(t.split()) <= 12)):
                b["type"] = "heading"
                b["section"] = t
                continue
            if FOOTNOTE_RE.match(t) and p["height"] and b["bbox"][1] > p["height"] * 0.80 and len(t) < 400:
                b["type"] = "footnote"
                continue
            b.setdefault("type", "body")
    # a page whose only content is the header/footer keeps furniture type; nothing else to do
    return pages


def compose(pages):
    """Body text in reading order, with [p..b..] anchors for citations."""
    lines = []
    for p in pages:
        for b in p["blocks"]:
            if b.get("type") == "furniture":
                continue
            lines.append({"id": b["id"], "page": p["page"], "type": b.get("type", "body"),
                          "section": b.get("section"), "text": b["text"].strip()})
    return lines


def extract(path, order="auto"):
    pages, engine, errors = load_pages(path)
    pages = strip_furniture(pages)
    pages, notes = order_blocks(pages, order=order)
    pages = tag_structures(pages)
    blocks = compose(pages)
    warnings = []
    if engine in ("pdftotext", "pypdf", "raw-stream"):
        warnings.append(f"engine {engine}: no text bounding boxes -> reading order is line-based only")
    if engine == "raw-stream":
        warnings.append("raw stream fallback: text order and characters may be unreliable, verify against the PDF")
    if not any(b["type"] == "reference" for b in blocks):
        warnings.append("no reference list detected: check whether the PDF has one or if it is image-only")
    return {"source": os.path.basename(path), "engine": engine, "engine_errors": errors,
            "page_count": len(pages), "warnings": warnings, "order_notes": notes,
            "pages": pages, "blocks": blocks}


def main():
    ap = argparse.ArgumentParser(description="PDF extraction with page-anchored evidence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("pdf"); ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--jsonl"); ex.add_argument("--order", default="auto", choices=["auto", "on", "off"])
    tb = sub.add_parser("tables")
    tb.add_argument("pdf"); tb.add_argument("-o", "--out", required=True)
    rf = sub.add_parser("refs")
    rf.add_argument("json"); rf.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    if a.cmd == "extract":
        res = extract(a.pdf, order=a.order)
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if a.jsonl:
            with open(a.jsonl, "w", encoding="utf-8") as fh:
                for b in res["blocks"]:
                    fh.write(json.dumps(b, ensure_ascii=False) + "\n")
        print(json.dumps({"engine": res["engine"], "pages": res["page_count"],
                          "blocks": len(res["blocks"]), "warnings": res["warnings"]}, ensure_ascii=False))
    elif a.cmd == "tables":
        json.dump(extract_tables(a.pdf), open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(a.out)
    else:
        data = json.load(open(a.json))
        refs = [b for b in data["blocks"] if b["type"] == "reference"]
        json.dump({"count": len(refs), "entries": refs}, open(a.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(json.dumps({"references": len(refs)}))


def extract_tables(path):
    """Table extraction with page anchors; pdfplumber first, then camelot, else honest failure."""
    out = {"source": os.path.basename(path), "engine": None, "tables": [], "warnings": []}
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                for ti, tbl in enumerate(page.extract_tables() or [], start=1):
                    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in tbl]
                    if not rows or all(not any(r) for r in rows):
                        continue
                    out["tables"].append({"page": pno, "table_index": ti, "rows": rows,
                                          "n_rows": len(rows), "n_cols": max(len(r) for r in rows)})
        out["engine"] = "pdfplumber"
    except Exception as exc:  # noqa: BLE001
        out["warnings"].append(f"pdfplumber unavailable/failed: {str(exc)[:120]}")
    if not out["tables"]:
        try:
            import camelot  # type: ignore
            for t in camelot.read_pdf(path, pages="all", flavor="lattice"):
                out["tables"].append({"page": int(t.page), "table_index": t.order + 1,
                                      "rows": t.df.values.tolist(), "n_rows": t.df.shape[0],
                                      "n_cols": t.df.shape[1], "accuracy": float(t.accuracy)})
            out["engine"] = (out["engine"] or "") + "+camelot"
        except Exception as exc:  # noqa: BLE001
            out["warnings"].append(f"camelot unavailable/failed: {str(exc)[:120]}")
    if not out["tables"]:
        out["warnings"].append("no table extracted: the PDF may use image tables or complex borders; "
                              "report this instead of inventing rows")
    return out


if __name__ == "__main__":
    main()
