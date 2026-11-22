#!/usr/bin/env python3
"""Render editable artifacts to real preview images, and audit the result.

The artifact tasks require an *editable* source plus a *real rendered preview*.
Rendering is attempted with whatever the pinned image provides, in order, and
the engine actually used is always reported - never assumed:

  office  : soffice/libreoffice --headless --convert-to pdf -> pdftoppm/mutool
  pdf     : pdftoppm -> mutool -> gs -> pymupdf
  svg     : rsvg-convert -> inkscape -> cairosvg -> soffice
  figure  : matplotlib (1x/2x, png + pdf)

CLI:
  python3 render.py office INPUT -o OUTDIR [--dpi 150]
  python3 render.py svg    INPUT.svg -o OUT.png [--width 1600]
  python3 render.py probe                      # report which engines exist
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile


def which(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def probe() -> dict:
    engines = {
        "soffice": which("soffice", "libreoffice") or "",
        "pdftoppm": which("pdftoppm") or "",
        "mutool": which("mutool") or "",
        "gs": which("gs") or "",
        "rsvg-convert": which("rsvg-convert") or "",
        "inkscape": which("inkscape") or "",
        "pdftotext": which("pdftotext") or "",
        "qpdf": which("qpdf") or "",
        "tesseract": which("tesseract") or "",
        "latex": which("pdflatex", "tectonic", "xelatex") or "",
        "unzip": which("unzip") or "",
    }
    mods = {}
    for name in ("matplotlib", "fitz", "pdfplumber", "pypdf", "PIL", "pptx", "docx", "numpy"):
        try:
            __import__(name)
            mods[name] = True
        except Exception:
            mods[name] = False
    engines["python_modules"] = mods
    return engines


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 127, str(exc)


def office_to_pdf(src: str, outdir: str) -> dict:
    soffice = which("soffice", "libreoffice")
    if not soffice:
        return {"ok": False, "engine": None, "error": "no soffice/libreoffice on PATH"}
    os.makedirs(outdir, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        code, log = run([soffice, "--headless", "--norestore", "--invisible",
                         f"-env:UserInstallation=file://{profile}",
                         "--convert-to", "pdf", "--outdir", outdir, os.path.abspath(src)])
    pdfs = sorted(glob.glob(os.path.join(outdir, "*.pdf")))
    if code != 0 or not pdfs:
        return {"ok": False, "engine": "soffice", "error": log[-400:] or f"exit {code}"}
    return {"ok": True, "engine": "soffice", "pdf": pdfs[0], "log": log[-200:]}


def pdf_to_png(pdf: str, outdir: str, dpi: int = 150, prefix: str = "page") -> dict:
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, prefix)
    ppm = which("pdftoppm")
    if ppm:
        code, log = run([ppm, "-r", str(dpi), "-png", pdf, base])
        pages = sorted(glob.glob(base + "*.png"))
        if code == 0 and pages:
            return {"ok": True, "engine": "pdftoppm", "pages": pages, "dpi": dpi}
    mutool = which("mutool")
    if mutool:
        code, log = run([mutool, "draw", "-r", str(dpi), "-o", base + "-%d.png", pdf])
        pages = sorted(glob.glob(base + "-*.png"))
        if code == 0 and pages:
            return {"ok": True, "engine": "mutool", "pages": pages, "dpi": dpi}
    gs = which("gs")
    if gs:
        code, log = run([gs, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m",
                         f"-r{dpi}", f"-sOutputFile={base}-%d.png", pdf])
        pages = sorted(glob.glob(base + "-*.png"))
        if code == 0 and pages:
            return {"ok": True, "engine": "gs", "pages": pages, "dpi": dpi}
    try:
        import fitz  # type: ignore
        doc = fitz.open(pdf)
        pages = []
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            path = f"{base}-{i}.png"
            pix.save(path)
            pages.append(path)
        if pages:
            return {"ok": True, "engine": "pymupdf", "pages": pages, "dpi": dpi}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "engine": None, "error": f"no pdf rasteriser: {exc}"}
    return {"ok": False, "engine": None, "error": "no pdf rasteriser available"}


def svg_to_png(src: str, out: str, width: int = 1600) -> dict:
    rsvg = which("rsvg-convert")
    if rsvg:
        code, log = run([rsvg, "-w", str(width), "-o", out, src])
        if code == 0 and os.path.isfile(out):
            return {"ok": True, "engine": "rsvg-convert", "png": out}
    ink = which("inkscape")
    if ink:
        code, log = run([ink, src, "-w", str(width), "-o", out])
        if code == 0 and os.path.isfile(out):
            return {"ok": True, "engine": "inkscape", "png": out}
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=src, write_to=out, output_width=width)
        if os.path.isfile(out):
            return {"ok": True, "engine": "cairosvg", "png": out}
    except Exception:
        pass
    soffice = which("soffice", "libreoffice")
    if soffice:
        with tempfile.TemporaryDirectory() as tmp:
            res = office_to_pdf(src, tmp)
            if res.get("ok"):
                png = pdf_to_png(res["pdf"], os.path.dirname(out) or ".", prefix=os.path.basename(out)[:-4])
                if png.get("ok"):
                    shutil.move(png["pages"][0], out)
                    return {"ok": True, "engine": "soffice+pdf", "png": out}
    return {"ok": False, "engine": None, "error": "no svg rasteriser available"}


def preview_bundle(src: str, outdir: str, dpi: int = 150) -> dict:
    """Full chain: editable source -> pdf -> png, with an explicit audit trail."""
    os.makedirs(outdir, exist_ok=True)
    report = {"source": os.path.abspath(src), "engines": probe(), "steps": []}
    ext = os.path.splitext(src)[1].lower()
    if ext == ".svg":
        png = os.path.join(outdir, "preview.png")
        res = svg_to_png(src, png, 1600)
        report["steps"].append({"svg_to_png": res})
        report["ok"] = bool(res.get("ok"))
        report["previews"] = [png] if res.get("ok") else []
        return report
    if ext == ".pdf":
        report["steps"].append({"copy": shutil.copy2(src, os.path.join(outdir, os.path.basename(src)))})
        pdf = os.path.join(outdir, os.path.basename(src))
    else:
        res = office_to_pdf(src, outdir)
        report["steps"].append({"office_to_pdf": res})
        if not res.get("ok"):
            report["ok"] = False
            report["previews"] = []
            return report
        pdf = res["pdf"]
    png = pdf_to_png(pdf, outdir, dpi=dpi)
    report["steps"].append({"pdf_to_png": png})
    report["ok"] = bool(png.get("ok"))
    report["previews"] = png.get("pages", []) if png.get("ok") else []
    report["page_count"] = len(report["previews"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Render editable artifacts to preview images")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    o = sub.add_parser("office")
    o.add_argument("src"); o.add_argument("-o", "--outdir", required=True)
    o.add_argument("--dpi", type=int, default=150)
    s = sub.add_parser("svg")
    s.add_argument("src"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--width", type=int, default=1600)
    a = ap.parse_args()
    if a.cmd == "probe":
        print(json.dumps(probe(), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "svg":
        print(json.dumps(svg_to_png(a.src, a.out, a.width), ensure_ascii=False))
        return 0
    report = preview_bundle(a.src, a.outdir, a.dpi)
    print(json.dumps({k: v for k, v in report.items() if k != "engines"}, ensure_ascii=False, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
