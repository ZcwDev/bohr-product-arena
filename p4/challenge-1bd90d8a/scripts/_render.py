#!/usr/bin/env python3
"""_render — turn the markdown deliverable into a PDF when the environment can, and say so when it cannot.

The briefs for the reading / Q&A / audit tasks list a PDF or a rendered page among the deliverables, while the
markdown text is what the engine can always produce. Rasterising needs a library (PyMuPDF or CairoSVG), and the
evaluation image keeps the scientific stack in its own interpreter, so this module tries the known interpreters
in order instead of assuming `sys.executable` has it.

Never fabricates a file: if no interpreter can render, the caller records `pdf: unavailable` with the reason.

usage (module):
    from _render import maybe_pdf
    info = maybe_pdf(out / "answer.md", out)      # -> {"pdf": "answer.pdf", "engine": "fitz"} or
                                                  #    {"pdf": None, "reason": "no renderer: ..."}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]

RENDER_PY = r'''
import sys, json
src, out = sys.argv[1], sys.argv[2]
fonts = json.loads(sys.argv[3])
try:
    import fitz
except Exception as exc:
    print(json.dumps({"ok": False, "reason": "no fitz: %s" % exc})); raise SystemExit(0)
lines = []
for raw in open(src, encoding="utf-8", errors="replace").read().splitlines():
    t = raw.rstrip()
    if t.startswith("#"):
        t = t.lstrip("#").strip()
    lines.append(t)
font = next((f for f in fonts if __import__("os").path.exists(f)), None)
doc = fitz.open()
page = None
margin, lh, width, height = 56, 16, 595, 842
y = margin
name = None
for t in lines:
    if page is None or y > height - margin:
        page = doc.new_page(width=width, height=height)
        y = margin
        if font and name is None:
            try:
                name = "cjk"
                page.insert_font(fontfile=font, fontname=name)
            except Exception:
                name = None
    try:
        if name:
            page.insert_text((margin, y), t[:110], fontsize=10.5, fontname=name)
        else:
            page.insert_text((margin, y), t.encode("ascii", "replace").decode(), fontsize=10.5)
    except Exception:
        pass
    y += lh
doc.save(out)
print(json.dumps({"ok": True, "pages": doc.page_count, "font": font}))
'''


def candidates() -> list[str]:
    seen, out = set(), []
    for c in (os.environ.get("WEEKLY_TOOLS_PYTHON"), "/opt/weekly-tools/bin/python",
              "/opt/weekly-tools/venv/bin/python", sys.executable, shutil.which("python3")):
        if c and c not in seen and Path(c).exists():
            seen.add(c)
            out.append(c)
    return out


SVG_RENDER_PY = r'''
import sys, json
src, png, pdf = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    import fitz
except Exception as exc:
    print(json.dumps({"ok": False, "reason": "no fitz: %s" % exc})); raise SystemExit(0)
try:
    doc = fitz.open(src)
except Exception as exc:
    print(json.dumps({"ok": False, "reason": "svg open: %s" % exc})); raise SystemExit(0)
try:
    if pdf:
        open(pdf, "wb").write(doc.convert_to_pdf())
    if png:
        doc[0].get_pixmap(dpi=200).save(png)
    print(json.dumps({"ok": True, "pages": doc.page_count}))
except Exception as exc:
    print(json.dumps({"ok": False, "reason": "render: %s" % exc}))
'''


def maybe_svg_outputs(svg_path: Path, out_dir: Path, stem: str = "figure") -> dict:
    """Rasterise an SVG to PNG and PDF through PyMuPDF (no cairo needed). Never raises."""
    if not svg_path.is_file():
        return {"png": None, "pdf": None, "reason": f"{svg_path.name} missing"}
    png, pdf = out_dir / f"{stem}.png", out_dir / f"{stem}.pdf"
    errors = []
    for py in candidates():
        try:
            p = subprocess.run([py, "-c", SVG_RENDER_PY, str(svg_path), str(png), str(pdf)],
                               capture_output=True, text=True, timeout=180)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{Path(py).name}: {type(exc).__name__}")
            continue
        info = {}
        for line in (p.stdout or "").splitlines()[::-1]:
            try:
                info = json.loads(line)
                break
            except Exception:
                continue
        if info.get("ok"):
            return {"png": png.name if png.is_file() else None,
                    "pdf": pdf.name if pdf.is_file() else None,
                    "engine": f"fitz via {Path(py).name}"}
        errors.append(f"{Path(py).name}: {info.get('reason') or (p.stderr or '').strip()[:60]}")
    return {"png": None, "pdf": None, "reason": "no svg renderer: " + "; ".join(errors[:3])}


def maybe_pdf(md_path: Path, out_dir: Path, stem: str | None = None) -> dict:
    """Render `md_path` into `<stem>.pdf` next to it, trying several interpreters. Never raises."""
    if not md_path.is_file():
        return {"pdf": None, "reason": f"{md_path.name} missing"}
    target = out_dir / f"{stem or md_path.stem}.pdf"
    errors = []
    for py in candidates():
        try:
            p = subprocess.run([py, "-c", RENDER_PY, str(md_path), str(target),
                                json.dumps(FONT_CANDIDATES)],
                               capture_output=True, text=True, timeout=180)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{py}: {type(exc).__name__}")
            continue
        info = {}
        for line in (p.stdout or "").splitlines()[::-1]:
            try:
                info = json.loads(line)
                break
            except Exception:
                continue
        if info.get("ok") and target.is_file() and target.stat().st_size > 500:
            return {"pdf": target.name, "engine": f"fitz via {Path(py).name}",
                    "pages": info.get("pages")}
        errors.append(f"{Path(py).name}: {info.get('reason') or (p.stderr or '').strip()[:60]}")
    return {"pdf": None, "reason": "no renderer: " + "; ".join(errors[:3])}
