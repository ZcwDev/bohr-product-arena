#!/usr/bin/env python3
"""Guaranteed previews for artifacts, even without LibreOffice on PATH.

`render.py` prefers the real office chain (soffice -> pdf -> png). When the
pinned image lacks it, a deliverable must still ship a *real* preview rather
than a claim that one exists. This module redraws the same specification with
matplotlib, so:

  * a diagram spec produces a faithful PNG of the same nodes/edges/layers;
  * a deck spec produces one PNG per slide (title, bullets, embedded figure);
  * a document spec produces one PNG per section block.

It never invents content: it renders exactly the spec that produced the
editable file, and reports which engine was used.

CLI:
  python3 preview.py diagram spec.json -o out.png [--pdf out.pdf]
  python3 preview.py slides  deck.json -o outdir/
  python3 preview.py doc     doc.json  -o outdir/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

PALETTE = ["#2F6FED", "#1B9C85", "#C2410C", "#6D28D9", "#0E7490", "#9A3412",
           "#3F6212", "#BE185D", "#0F766E", "#7C2D12"]
EDGE_KIND = {"activation": ("-", ">"), "inhibition": ("--", "|"), "transport": ("-", ">"),
             "binding": ("-", None), "conversion": ("-", ">"), "association": ("--", None),
             "phosphorylation": ("-", ">"), "default": ("-", ">")}



def use_cjk_font():
    """Pick an installed CJK font so Chinese labels render instead of tofu boxes."""
    try:
        import matplotlib
        from matplotlib import font_manager
    except Exception:
        return None
    wanted = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC", "Source Han Sans CN",
              "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Microsoft YaHei", "SimHei",
              "PingFang SC", "Heiti SC", "Arial Unicode MS", "Droid Sans Fallback")
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in wanted:
        if name in installed:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    return None


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    use_cjk_font()
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Ellipse
    return matplotlib, plt, FancyArrowPatch, FancyBboxPatch, Ellipse


def diagram_png(spec: dict, png_path: str, pdf_path: str | None = None) -> dict:
    matplotlib, plt, FancyArrowPatch, FancyBboxPatch, Ellipse = _mpl()
    width = float(spec.get("width") or 1180) / 100.0
    nodes = {str(n["id"]): n for n in spec.get("nodes", [])}
    edges = spec.get("edges", [])
    layers = spec.get("layers")
    if not layers:
        depth = {nid: 0 for nid in nodes}
        for _ in range(len(nodes) + 1):
            changed = False
            for e in edges:
                s, d = str(e.get("from")), str(e.get("to"))
                if s in depth and d in depth and depth[d] < depth[s] + 1:
                    depth[d] = depth[s] + 1
                    changed = True
            if not changed:
                break
        layers = []
        for nid in nodes:
            d = depth.get(nid, 0)
            while len(layers) <= d:
                layers.append([])
            layers[d].append(nid)
    node_w = float(spec.get("node_width") or 190) / 100.0
    node_h = float(spec.get("node_height") or 68) / 100.0
    gap = float(spec.get("layer_gap") or 90) / 100.0
    height = 1.6 + len(layers) * (node_h + gap)
    fig, ax = plt.subplots(figsize=(width, height), dpi=200)
    ax.set_xlim(0, width); ax.set_ylim(0, height); ax.axis("off")
    if spec.get("title"):
        ax.text(width / 2, height - 0.35, spec["title"], ha="center", va="center",
                fontsize=15, fontweight="bold")
    if spec.get("subtitle"):
        ax.text(width / 2, height - 0.68, spec["subtitle"], ha="center", va="center",
                fontsize=9, color="#475569")
    pos = {}
    pad = 0.9
    layer_of = {}
    for li, layer in enumerate(layers):
        y = height - 1.5 - li * (node_h + gap) - node_h / 2
        n = max(1, len(layer))
        span = width - 2 * pad
        for i, nid in enumerate(layer):
            x = pad + (span * (i + 0.5) / n if n > 1 else span / 2) + pad * 0
            pos[nid] = (x if n > 1 else width / 2, y)
            layer_of[nid] = li
    for name, idx in zip(spec.get("compartments") or [], range(len(layers))):
        ax.text(0.12, height - 1.5 - idx * (node_h + gap) - node_h / 2, name, ha="left",
                va="center", fontsize=8, color="#64748B")
    for e in edges:
        s, d = str(e.get("from")), str(e.get("to"))
        if s not in pos or d not in pos:
            continue
        x1, y1 = pos[s]; x2, y2 = pos[d]
        kind = str(e.get("type") or "default")
        ls, head = EDGE_KIND.get(kind, EDGE_KIND["default"])
        arrow = FancyArrowPatch((x1, y1 - node_h / 2), (x2, y2 + node_h / 2),
                                arrowstyle="-|>" if head == ">" else "-", mutation_scale=12,
                                linestyle="--" if ls == "--" else "solid",
                                color="#334155", linewidth=1.2, zorder=1,
                                connectionstyle="arc3,rad=0.0")
        ax.add_patch(arrow)
        if head == "|":
            half = 0.10
            ax.plot([x2 - half, x2 + half], [y2 + node_h / 2, y2 + node_h / 2],
                    color="#334155", linewidth=2.2, zorder=2)
        label = e.get("label")
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2, str(label), ha="center", va="center",
                    fontsize=7.5, color="#334155",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))
    for nid, node in nodes.items():
        if nid not in pos:
            continue
        cx, cy = pos[nid]
        kind = str(node.get("kind") or "box")
        raw = node.get("color") or node.get("fill")
        if isinstance(raw, str) and raw.startswith("#"):
            color = raw
        else:
            idx = raw if isinstance(raw, int) else layer_of.get(nid, 0)
            color = PALETTE[idx % len(PALETTE)]
        shape = "round,pad=0.02,rounding_size=0.12" if kind != "process" else "round,pad=0.02,rounding_size=0.5"
        if kind in ("ellipse", "metabolite", "compound"):
            ax.add_patch(Ellipse((cx, cy), node_w, node_h, facecolor=color + "22",
                                 edgecolor=color, linewidth=1.6))
        else:
            ax.add_patch(FancyBboxPatch((cx - node_w / 2, cy - node_h / 2), node_w, node_h,
                                        boxstyle=shape, facecolor=color + "22",
                                        edgecolor=color, linewidth=1.6,
                                        linestyle="--" if kind == "compartment" else "solid"))
        label = str(node.get("label") or nid)
        ax.text(cx, cy, "\n".join(textwrap.wrap(label, max(8, int(node_w * 6)))),
                ha="center", va="center", fontsize=8.5, fontweight="bold", zorder=3)
        if node.get("note"):
            ax.text(cx, cy - node_h / 2 - 0.16, str(node["note"]), ha="center", va="top",
                    fontsize=6.5, color="#64748B")
    legend = spec.get("legend")
    if legend:
        for i, item in enumerate(legend):
            y = 0.5 + i * 0.28
            kind = str(item.get("type") or "default")
            ls, head = EDGE_KIND.get(kind, EDGE_KIND["default"])
            ax.plot([0.15, 0.6], [y, y], linestyle="--" if ls == "--" else "-", color="#334155",
                    linewidth=1.2)
            ax.text(0.68, y, str(item.get("name") or item.get("label") or ""), fontsize=7,
                    va="center", ha="left", color="#334155")
    fig.tight_layout(pad=0.3)
    fig.savefig(png_path, dpi=200, facecolor="white", bbox_inches="tight")
    if pdf_path:
        fig.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close(fig)
    return {"ok": True, "engine": "matplotlib", "png": png_path, "pdf": pdf_path}


def slides_png(spec: dict, outdir: str) -> dict:
    matplotlib, plt, FancyArrowPatch, FancyBboxPatch, Ellipse = _mpl()
    os.makedirs(outdir, exist_ok=True)
    slides = spec.get("slides") or []
    pages = []
    for i, slide in enumerate(slides, start=1):
        fig, ax = plt.subplots(figsize=(13.333, 7.5), dpi=110)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
        ax.add_patch(plt.Rectangle((0.02, 0.02), 0.96, 0.96, fill=False, ec="#E2E8F0", lw=1))
        title = str(slide.get("title") or f"Slide {i}")
        ax.text(0.06, 0.90, title, fontsize=22, fontweight="bold", va="top", ha="left", color="#0F172A")
        ax.plot([0.06, 0.94], [0.855, 0.855], color="#2F6FED", linewidth=2)
        bullets = slide.get("bullets") or []
        image = slide.get("image")
        text_w = 0.86 if not image else 0.42
        for j, b in enumerate(bullets):
            text = str(b)
            indent = 0.06 + (0.03 if text.startswith(("- ", "* ")) else 0)
            ax.text(indent, 0.79 - j * 0.083, "\u2022 " + text.lstrip("-* "), fontsize=12,
                    va="top", ha="left", color="#1E293B")
        table = slide.get("table")
        if isinstance(table, dict) and table.get("rows"):
            rows = ([table.get("header")] if table.get("header") else []) + list(table["rows"])
            ncol = max(len(r) for r in rows) or 1
            x0 = 0.06
            width = 0.86 if not image else 0.40
            top = 0.76 - len(bullets) * 0.083 - 0.05
            row_h = min(0.052, (top - 0.12) / max(1, len(rows)))
            col_w = width / ncol
            for ri, row in enumerate(rows):
                y_top = top - ri * row_h
                for ci in range(ncol):
                    cell = str(row[ci]) if ci < len(row) else ""
                    ax.add_patch(plt.Rectangle((x0 + ci * col_w, y_top - row_h), col_w, row_h,
                                               facecolor="#E8EEFA" if ri == 0 else "white",
                                               edgecolor="#94A3B8", linewidth=0.6, zorder=2))
                    ax.text(x0 + ci * col_w + col_w / 2, y_top - row_h / 2, cell, fontsize=8.5,
                            ha="center", va="center", zorder=3,
                            fontweight="bold" if ri == 0 else "normal", color="#0F172A")
        if image and os.path.isfile(image):
            try:
                img = plt.imread(image)
                ax.imshow(img, extent=(0.50, 0.96, 0.08, 0.82), aspect="auto", zorder=2)
            except Exception as exc:  # noqa: BLE001
                ax.text(0.52, 0.6, f"[figure not renderable: {exc}]", fontsize=9, color="#B91C1C")
        if slide.get("caption"):
            ax.text(0.06, 0.06, str(slide["caption"]), fontsize=8, color="#64748B", va="bottom")
        page = os.path.join(outdir, f"slide-{i:02d}.png")
        fig.savefig(page, dpi=110, facecolor="white")
        plt.close(fig)
        pages.append(page)
    return {"ok": bool(pages), "engine": "matplotlib", "pages": pages, "count": len(pages)}


def doc_png(spec: dict, outdir: str) -> dict:
    """Render a document spec as one long page image per 1000 characters."""
    matplotlib, plt, _, _, _ = _mpl()
    os.makedirs(outdir, exist_ok=True)
    blocks = list(spec.get("blocks") or [])
    for s in spec.get("slides") or []:
        if s.get("title"):
            blocks.append({"type": "h1", "text": s["title"]})
        for b in s.get("bullets") or []:
            blocks.append({"type": "bullet", "text": str(b)})
    pages, page_lines, limit = [], [], 44
    for block in blocks:
        kind = (block.get("type") or "p").lower()
        prefix = {"h1": "", "h2": "", "bullet": "\u2022 ", "li": "\u2022 "}.get(kind, "")
        for line in textwrap.wrap(str(block.get("text") or ""), 92) or [""]:
            page_lines.append((kind, prefix + line))
    for start in range(0, max(1, len(page_lines)), limit):
        chunk = page_lines[start:start + limit]
        fig, ax = plt.subplots(figsize=(8.27, 11.69), dpi=110)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
        y = 0.95
        for kind, line in chunk:
            if kind == "h1":
                ax.text(0.08, y, line, fontsize=15, fontweight="bold", va="top")
                y -= 0.05
            elif kind == "h2":
                ax.text(0.08, y, line, fontsize=12.5, fontweight="bold", va="top")
                y -= 0.038
            else:
                ax.text(0.08, y, line, fontsize=9.5, va="top", color="#1E293B")
                y -= 0.021
        page = os.path.join(outdir, f"doc-{start // limit + 1:02d}.png")
        fig.savefig(page, dpi=110, facecolor="white")
        plt.close(fig)
        pages.append(page)
    return {"ok": bool(pages), "engine": "matplotlib", "pages": pages, "count": len(pages)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Guaranteed matplotlib previews")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagram"); d.add_argument("spec"); d.add_argument("-o", "--out", required=True)
    d.add_argument("--pdf")
    s = sub.add_parser("slides"); s.add_argument("spec"); s.add_argument("-o", "--outdir", required=True)
    o = sub.add_parser("doc"); o.add_argument("spec"); o.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    if a.cmd == "diagram":
        print(json.dumps(diagram_png(spec, a.out, a.pdf), ensure_ascii=False))
    elif a.cmd == "slides":
        print(json.dumps(slides_png(spec, a.outdir), ensure_ascii=False))
    else:
        print(json.dumps(doc_png(spec, a.outdir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
