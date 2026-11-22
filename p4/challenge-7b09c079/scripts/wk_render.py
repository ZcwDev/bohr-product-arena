#!/usr/bin/env python3
"""Preview rendering with an explicit degradation ladder (the path used is reported).

  docx -> PDF : 1. soffice/libreoffice --headless --convert-to pdf   (real Office layout)
                2. pure-Python PDF writer built from the same block list
                   (Adobe-standard CID font STSong-Light/UniGB-UCS2-H, no embedding,
                   so CJK + Latin both render in any conformant viewer)
  PDF  -> PNG : 1. pdftoppm   2. PyMuPDF (fitz)
                3. pure-stdlib PNG "layout raster" drawn from the same page geometry
                   (line boxes / annotation boxes at their real coordinates; it is a
                   geometry preview, not a text raster, and is labelled as such)
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

A4 = (595.0, 842.0)
MARGIN = 60.0


def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice", "/usr/bin/soffice",
              "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"):
        if n in ("soffice", "libreoffice") and os.path.exists(p):
            return p
    return None


def probe() -> dict:
    info = {"soffice": which("soffice", "libreoffice") or "missing",
            "pdftoppm": which("pdftoppm") or "missing"}
    for mod in ("fitz", "docx", "matplotlib"):
        try:
            __import__(mod)
            info[mod] = "available"
        except Exception:
            info[mod] = "missing"
    return info


# ---------------------------------------------------------------- soffice
def office_to_pdf(src: Path, outdir: Path, timeout: int = 180) -> dict:
    exe = which("soffice", "libreoffice")
    if not exe:
        return {"ok": False, "engine": "soffice", "error": "soffice/libreoffice not found"}
    outdir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
           "--convert-to", "pdf", "--outdir", str(outdir), str(src)]
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "engine": "soffice", "error": f"timeout after {timeout}s", "cmd": cmd}
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = outdir / (src.stem + ".pdf")
    if cp.returncode == 0 and pdf.is_file() and pdf.stat().st_size > 0:
        return {"ok": True, "engine": "soffice", "pdf": str(pdf), "exit_code": 0, "cmd": " ".join(cmd[:1] + cmd[2:])}
    return {"ok": False, "engine": "soffice", "exit_code": cp.returncode,
            "error": (cp.stderr or cp.stdout).decode("utf-8", "replace")[-400:]}


def pdf_to_png(pdf: Path, outdir: Path, prefix: str, dpi: int = 110, max_pages: int = 6,
               pages: list[int] | None = None) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    wanted = pages or list(range(1, max_pages + 1))
    ppm = which("pdftoppm")
    if ppm:
        made = []
        for p in wanted:
            stem = outdir / f"{prefix}-p{p:02d}"
            cp = subprocess.run([ppm, "-png", "-r", str(dpi), "-f", str(p), "-l", str(p), "-singlefile",
                                 str(pdf), str(stem)], capture_output=True, timeout=120)
            f = Path(str(stem) + ".png")
            if cp.returncode == 0 and f.is_file():
                made.append(str(f))
        if made:
            return {"ok": True, "engine": "pdftoppm", "pngs": made, "dpi": dpi}
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf))
        made = []
        for p in wanted:
            if p <= doc.page_count:
                f = outdir / f"{prefix}-p{p:02d}.png"
                doc[p - 1].get_pixmap(dpi=dpi).save(str(f))
                made.append(str(f))
        if made:
            return {"ok": True, "engine": "fitz", "pngs": made, "dpi": dpi}
    except Exception as exc:  # noqa: BLE001
        err = repr(exc)
    else:
        err = "no pages"
    return {"ok": False, "engine": "none", "error": err}


def pdf_page_count(pdf: Path) -> int:
    data = pdf.read_bytes()
    m = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data, re.S)
    if m:
        return max(int(x) for x in m)
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))


# ---------------------------------------------------------------- pure PDF
def _w(ch: str, size: float) -> float:
    o = ord(ch)
    if o > 0x2E80:
        return size
    if ch in "il.,:;'|!":
        return size * 0.28
    if ch.isupper() or ch in "mwMW@%":
        return size * 0.68
    return size * 0.5


def _wrap(text: str, size: float, width: float) -> list[str]:
    lines, cur, cw = [], "", 0.0
    for tok in re.findall(r"[\u2e80-\uffff]|\S+\s*|\s+", text):
        tw = sum(_w(c, size) for c in tok)
        if cw + tw > width and cur.strip():
            lines.append(cur.rstrip())
            cur, cw = tok.lstrip(), sum(_w(c, size) for c in tok.lstrip())
            while cw > width and len(cur) > 1:  # hard-break very long tokens
                k = max(1, int(len(cur) * width / cw))
                lines.append(cur[:k]); cur = cur[k:]; cw = sum(_w(c, size) for c in cur)
        else:
            cur += tok; cw += tw
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def layout(blocks: list[dict], header: str = "", footer: str = "") -> list[list[dict]]:
    """blocks: [{'text','role'}] role in title|h1|h2|h3|body|caption|table|equation|annotation|ref.

    Returns pages -> list of draw ops {x,y,size,text,bold,color,box}."""
    styles = {"title": (16, True), "h1": (14, True), "h2": (12.5, True), "h3": (11.5, True),
              "body": (10, False), "caption": (9, False), "table": (8.5, False), "equation": (10, False),
              "annotation": (9, False), "ref": (8.5, False)}
    width = A4[0] - 2 * MARGIN
    pages, ops, y = [], [], A4[1] - MARGIN

    def newpage():
        nonlocal ops, y
        if ops:
            pages.append(ops)
        ops, y = [], A4[1] - MARGIN

    for b in blocks:
        size, bold = styles.get(b.get("role", "body"), (10, False))
        indent = 14.0 if b.get("role") == "annotation" else 0.0
        text = re.sub(r"\*\*|__|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)|`", "", b["text"])
        raw_lines = text.splitlines() if b.get("role") in ("table", "equation", "ref") else [" ".join(text.split())]
        lines = []
        for rl in raw_lines:
            lines.extend(_wrap(rl, size, width - indent - (8 if indent else 0)))
        lead = size * 1.35
        need = lead * len(lines) + size * 0.8
        if y - need < MARGIN + 20 and y < A4[1] - MARGIN - 1:
            newpage()
        if b.get("role") == "annotation":
            top = y + size * 0.2
            h = lead * len(lines) + 6
            ops.append({"box": (MARGIN + 4, top - h, width - 4, h), "fill": (0.92, 0.95, 0.99),
                        "stroke": (0.12, 0.44, 0.70)})
        if b.get("role") in ("h1", "h2", "title"):
            y -= size * 0.5
        for ln in lines:
            if y - lead < MARGIN:
                newpage()
            y -= lead
            x = MARGIN + indent + (4 if indent else 0)
            if b.get("role") in ("title", "equation"):
                x = MARGIN + max(0.0, (width - sum(_w(c, size) for c in ln)) / 2)
            ops.append({"x": x, "y": y, "size": size, "text": ln, "bold": bold,
                        "color": (0.12, 0.23, 0.34) if b.get("role") == "annotation" else (0, 0, 0)})
        y -= size * 0.7
    newpage()
    for i, p in enumerate(pages, 1):
        if header:
            p.append({"x": MARGIN, "y": A4[1] - 34, "size": 7.5, "text": header[:120], "bold": False,
                      "color": (0.4, 0.4, 0.4)})
        p.append({"x": A4[0] / 2 - 30, "y": 30, "size": 7.5, "text": f"{footer} {i}/{len(pages)}".strip(),
                  "bold": False, "color": (0.4, 0.4, 0.4)})
    return pages


def write_pdf(pages: list[list[dict]], out: Path) -> dict:
    objs = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H "
               b"/DescendantFonts [ 3 0 R ] >>")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # 2: unused placeholder id keeper
    add(b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo "
        b"<< /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> /FontDescriptor 4 0 R /DW 1000 "
        b"/W [ 1 95 500 ] >>")
    add(b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [ -25 -254 1000 880 ] "
        b"/ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 93 >>")
    pages_id_placeholder = len(objs) + 1
    add(b"")  # Pages, filled later
    kids = []
    for ops in pages:
        cs = []
        for op in ops:
            if "box" in op:
                x, y, w, h = op["box"]
                cs.append("q %.3f %.3f %.3f rg %.3f %.3f %.3f RG 0.8 w %.1f %.1f %.1f %.1f re B Q" % (
                    *op["fill"], *op["stroke"], x, y, w, h))
                continue
            hexs = "".join("%04X" % (ord(c) if ord(c) <= 0xFFFF else 0x3F) for c in op["text"])
            r, g, b = op["color"]
            mode = "2 Tr 0.25 w" if op.get("bold") else "0 Tr"
            cs.append("BT %.3f %.3f %.3f rg %.3f %.3f %.3f RG %s /F1 %.1f Tf %.1f %.1f Td <%s> Tj ET" % (
                r, g, b, r, g, b, mode, op["size"], op["x"], op["y"], hexs))
        stream = zlib.compress("\n".join(cs).encode("latin-1"))
        cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R >> >> "
                        b"/Contents %d 0 R >>" % (pages_id_placeholder, A4[0], A4[1], font, cid)))
    objs[pages_id_placeholder - 1] = b"<< /Type /Pages /Kids [ %s ] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id_placeholder)
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(buf)
    buf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        buf += b"%010d 00000 n \n" % o
    buf += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    out.write_bytes(bytes(buf))
    return {"ok": True, "engine": "pure-python-pdf", "pdf": str(out), "pages": len(pages)}


# ---------------------------------------------------------------- pure PNG
def write_png_geometry(page_ops: list[dict], out: Path, scale: float = 1.2) -> None:
    W, H = int(A4[0] * scale), int(A4[1] * scale)
    px = bytearray(b"\xff" * (W * H * 3))

    def rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, int(x0)), min(W, int(x1))
        y0, y1 = max(0, int(y0)), min(H, int(y1))
        c = bytes(col)
        for yy in range(y0, y1):
            row = yy * W * 3
            px[row + x0 * 3:row + x1 * 3] = c * (x1 - x0)

    for op in page_ops:
        if "box" in op:
            x, y, w, h = op["box"]
            X0, X1 = x * scale, (x + w) * scale
            Y0, Y1 = (A4[1] - y - h) * scale, (A4[1] - y) * scale
            rect(X0, Y0, X1, Y1, [int(v * 255) for v in op["fill"]])
            rect(X0, Y0, X0 + 3, Y1, [int(v * 255) for v in op["stroke"]])
    for op in page_ops:
        if "box" in op:
            continue
        x = op["x"] * scale
        ybase = (A4[1] - op["y"]) * scale
        hgt = op["size"] * 0.62 * scale
        col = [int(v * 255) for v in op["color"]] if op["color"] != (0, 0, 0) else [70, 70, 70]
        if op.get("bold"):
            col = [20, 20, 20]
        # one bar per word at its real advance width
        cx = x
        for tok in re.findall(r"[\u2e80-\uffff]|\S+|\s+", op["text"]):
            tw = sum(_w(c, op["size"]) for c in tok) * scale
            if not tok.isspace():
                rect(cx, ybase - hgt, cx + tw - 0.6 * scale, ybase, col)
            cx += tw
    raw = b"".join(b"\x00" + bytes(px[r * W * 3:(r + 1) * W * 3]) for r in range(H))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    out.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render_document(docx: Path, blocks: list[dict], outdir: Path, prefix: str,
                    header: str = "", footer: str = "", focus_pages_text: list[str] | None = None,
                    allow_office: bool = True) -> dict:
    """Produce <prefix>.pdf + PNG pages; returns a receipt with the ladder actually taken."""
    outdir.mkdir(parents=True, exist_ok=True)
    rec: dict = {"attempts": []}
    pdf_path = outdir / f"{prefix}.pdf"
    pages_ops = None
    if allow_office:
        tmp = outdir / "_lo"
        r = office_to_pdf(docx, tmp)
        rec["attempts"].append({k: v for k, v in r.items() if k != "cmd"})
        if r.get("ok"):
            shutil.move(r["pdf"], pdf_path)
            shutil.rmtree(tmp, ignore_errors=True)
            rec["pdf_engine"] = "soffice"
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if "pdf_engine" not in rec:
        pages_ops = layout(blocks, header, footer)
        write_pdf(pages_ops, pdf_path)
        rec["pdf_engine"] = "pure-python-pdf (layout from the same block list as the DOCX)"
    rec["pdf"] = str(pdf_path)
    n = pdf_page_count(pdf_path)
    rec["pdf_pages"] = n
    # choose pages: first page + pages that contain the annotations
    wanted = [1]
    if focus_pages_text:
        try:
            from wk_io import pdf_pages, norm_cmp
            pg, _ = pdf_pages(pdf_path)
            for needle in focus_pages_text:
                nd = norm_cmp(needle)[:40]
                for i, p in enumerate(pg, 1):
                    if nd and nd in norm_cmp(" ".join(b["text"] for b in p)):
                        wanted.append(i)
                        break
        except Exception as exc:  # noqa: BLE001
            rec["focus_page_search_error"] = repr(exc)
    wanted = sorted(set(p for p in wanted if 1 <= p <= max(n, 1)))[:8]
    rec["preview_pages"] = wanted
    r = pdf_to_png(pdf_path, outdir, prefix, pages=wanted)
    if r.get("ok"):
        rec["png_engine"] = r["engine"]
        rec["pngs"] = r["pngs"]
    else:
        rec["attempts"].append(r)
        if pages_ops is None:
            pages_ops = layout(blocks, header, footer)
        made = []
        for p in wanted:
            if p <= len(pages_ops):
                f = outdir / f"{prefix}-p{p:02d}.png"
                write_png_geometry(pages_ops[p - 1], f)
                made.append(str(f))
        rec["png_engine"] = "pure-stdlib geometry raster (word boxes at real layout coordinates; read text in the PDF)"
        rec["pngs"] = made
    return rec
