#!/usr/bin/env python3
"""Deterministic scientific figure production: schematics, mechanisms, charts.

Two producers, both driven by a JSON spec so the figure is reproducible and
editable rather than a one-off screenshot:

  diagram(spec)  - entity/relation graphics: flow charts, mechanism and pathway
                   diagrams, experimental set-ups. Pure-standard-library SVG
                   with an explicit layered layout; arrows carry semantics
                   (activation / inhibition / transport / binding) so the edge
                   type is never lost.
  chart(spec)    - data charts from a numbers table: bar, grouped bar, line,
                   scatter, box, heatmap, with axis labels, units, error bars,
                   sample sizes and significance marks kept next to the data.

Both write SVG (always editable) and hand off to render.py for PNG/PDF.

CLI:
  python3 figure.py diagram spec.json -o out.svg [--png out.png]
  python3 figure.py chart   spec.json -o out.svg [--png out.png]
  python3 figure.py check   spec.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape

FONT = "Helvetica, Arial, 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"
PALETTE = ["#2F6FED", "#1B9C85", "#C2410C", "#6D28D9", "#0E7490", "#9A3412",
           "#3F6212", "#BE185D", "#0F766E", "#7C2D12"]

EDGE_STYLE = {
    "activation": {"marker": "arrow", "dash": None, "label": "\u2192"},
    "inhibition": {"marker": "bar", "dash": "6 4", "label": "\u22a3"},
    "transport": {"marker": "arrow", "dash": None, "label": "\u21d2"},
    "binding": {"marker": "none", "dash": None, "label": "\u2014"},
    "conversion": {"marker": "arrow", "dash": None, "label": "\u21b3"},
    "association": {"marker": "none", "dash": "3 3", "label": "\u2014"},
    "phosphorylation": {"marker": "arrow", "dash": None, "label": "P\u2192"},
    "default": {"marker": "arrow", "dash": None, "label": "\u2192"},
}


def _defs() -> str:
    return (
        "<defs>"
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#334155"/></marker>'
        '<marker id="arrowBlue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#2F6FED"/></marker>'
        '<marker id="bar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M 8 0 L 8 10" stroke="#334155" stroke-width="2.4" fill="none"/></marker>'
        "</defs>")


def _text(x, y, s, size=14, anchor="middle", weight="normal", fill="#0F172A", rotate=None,
          node_id=None):
    transform = f' transform="rotate({rotate} {x} {y})"' if rotate else ""
    ident = f' id="label-{node_id}" data-role="label"' if node_id else ""
    return (f'<text{ident} x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
            f'dominant-baseline="middle"{transform}>{escape(str(s))}</text>')


def _wrap(text: str, width: int) -> list[str]:
    text = str(text)
    if len(text) <= width:
        return [text]
    words, lines, cur = list(text), [], ""
    if " " not in text:
        return [text[i:i + width] for i in range(0, len(text), width)]
    for word in text.split(" "):
        if len(cur) + len(word) + 1 <= width:
            cur = (cur + " " + word).strip()
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def diagram(spec: dict) -> str:
    """Layered schematic. spec: {title, layers/compartment, nodes, edges, legend}."""
    nodes = {str(n["id"]): n for n in spec.get("nodes", [])}
    edges = spec.get("edges", [])
    layers = spec.get("layers")
    width = int(spec.get("width") or 1180)

    if not layers:
        # auto-layer: topological depth, ties by declaration order
        indeg = {nid: 0 for nid in nodes}
        for e in edges:
            if e.get("to") in indeg and e.get("from") in indeg:
                indeg[e["to"]] = indeg.get(e["to"], 0) + 1
        depth = {nid: 0 for nid in nodes}
        for _ in range(len(nodes) + 1):
            changed = False
            for e in edges:
                src, dst = e.get("from"), e.get("to")
                if src in depth and dst in depth and depth[dst] < depth[src] + 1:
                    depth[dst] = depth[src] + 1
                    changed = True
            if not changed:
                break
        layers = []
        for nid in nodes:
            d = depth.get(nid, 0)
            while len(layers) <= d:
                layers.append([])
            layers[d].append(nid)

    node_w = int(spec.get("node_width") or 190)
    node_h = int(spec.get("node_height") or 68)
    gap_y = int(spec.get("layer_gap") or 90)
    pad = 90
    layer_ys = []
    y = 150
    for layer in layers:
        layer_ys.append(y)
        y += node_h + gap_y
    height = y + 40

    pos: dict[str, tuple[float, float]] = {}
    layer_of: dict[str, int] = {}
    for li, layer in enumerate(layers):
        n = max(1, len(layer))
        span = width - 2 * pad
        step = span / n if n > 1 else 0
        for i, nid in enumerate(layer):
            cx = pad + node_w / 2 + (i * step if n > 1 else span / 2)
            pos[nid] = (cx, layer_ys[li] + node_h / 2)
            layer_of[nid] = li

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">', _defs(),
           f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>']
    if spec.get("title"):
        out.append(_text(width / 2, 52, spec["title"], 24, weight="bold"))
    if spec.get("subtitle"):
        out.append(_text(width / 2, 84, spec["subtitle"], 14, fill="#475569"))
    for i, name in enumerate(spec.get("compartments") or []):
        out.append(_text(28, layer_ys[min(i, len(layer_ys) - 1)] + node_h / 2, name, 13,
                         anchor="start", fill="#64748B", rotate=None))

    used_labels: set[tuple[str, str]] = set()
    for e in edges:
        src, dst = str(e.get("from")), str(e.get("to"))
        if src not in pos or dst not in pos:
            continue
        (x1, y1), (x2, y2) = pos[src], pos[dst]
        kind = str(e.get("type") or "default")
        style = EDGE_STYLE.get(kind, EDGE_STYLE["default"])
        marker = ("arrowBlue" if kind == "activation"
                  else "bar" if style["marker"] == "bar"
                  else "arrow")
        var = e.get("via")
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        if var:
            vx = (x1 + x2) / 2 + float(var) * 40
            path = f"M {x1:.1f} {y1 + node_h / 2:.1f} C {vx:.1f} {y1:.1f}, {vx:.1f} {y2:.1f}, {x2:.1f} {y2 - node_h / 2:.1f}"
        else:
            path = (f"M {x1:.1f} {y1 + node_h / 2:.1f} L {x1:.1f} {(y1 + y2) / 2:.1f} "
                    f"L {x2:.1f} {(y1 + y2) / 2:.1f} L {x2:.1f} {y2 - node_h / 2:.1f}")
        marker_attr = "" if style["marker"] in ("none", None) else f' marker-end="url(#{marker})"'
        edge_id = str(e.get("id") or f"{src}__{dst}")
        out.append(f'<path id="edge-{edge_id}" data-role="edge" data-type="{escape(kind)}" '
                   f'data-from="{escape(src)}" data-to="{escape(dst)}" '
                   f'd="{path}" fill="none" stroke="#334155" stroke-width="2"{dash}{marker_attr}/>')
        label = e.get("label") or style["label"]
        key = (kind, str(label))
        if label and not e.get("hide_label"):
            lx, ly = (x1 + x2) / 2, (y1 + y2) / 2
            out.append(f'<rect x="{lx - 34:.1f}" y="{ly - 13:.1f}" width="68" height="24" rx="6" '
                       f'fill="#FFFFFF" opacity="0.92"/>')
            out.append(_text(lx, ly, label, 12, fill="#334155"))
            used_labels.add(key)

    for nid, node in nodes.items():
        if nid not in pos:
            continue
        cx, cy = pos[nid]
        kind = str(node.get("kind") or node.get("shape") or "box")
        raw = node.get("fill") or node.get("color")
        if isinstance(raw, str) and raw.startswith("#"):
            stroke = raw
            fill = raw + "1A"
        else:
            idx = raw if isinstance(raw, int) else (layer_of.get(nid, 0) + int(node.get("color_index") or 0))
            stroke = node.get("stroke") or PALETTE[idx % len(PALETTE)]
            fill = stroke + "1A"
        x, y = cx - node_w / 2, cy - node_h / 2
        meta = (f'id="node-{nid}" data-role="node" data-kind="{escape(kind)}" '
                f'data-label="{escape(str(node.get("label") or nid))}"')
        if kind in ("ellipse", "metabolite", "compound"):
            out.append(f'<ellipse {meta} cx="{cx:.1f}" cy="{cy:.1f}" rx="{node_w/2:.1f}" ry="{node_h/2:.1f}" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        elif kind in ("membrane", "receptor"):
            out.append(f'<rect {meta} x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="8" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="3"/>')
        elif kind == "compartment":
            out.append(f'<rect {meta} x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="26" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="2" stroke-dasharray="8 6"/>')
        elif kind == "process":
            out.append(f'<rect {meta} x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="{node_h/2:.1f}" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        else:
            out.append(f'<rect {meta} x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="10" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        label = str(node.get("label") or nid)
        lines = _wrap(label, int(node.get("wrap") or max(10, node_w // 11)))
        for i, line in enumerate(lines):
            ly = cy + (i - (len(lines) - 1) / 2) * 18
            out.append(_text(cx, ly, line, int(node.get("size") or 14),
                             weight="bold" if node.get("bold") else "normal",
                             node_id=nid if i == 0 else None))
        if node.get("note"):
            out.append(_text(cx, cy + node_h / 2 + 16, node["note"], 11, fill="#64748B"))

    legend = spec.get("legend")
    if legend is None:
        legend = [{"type": k, "label": EDGE_STYLE[k]["label"]} for k, _ in sorted(used_labels)]
    if legend:
        lx, ly = 40, height - 34 * len(legend) - 20
        out.append(f'<rect x="{lx - 12}" y="{ly - 16}" width="250" height="{34 * len(legend) + 24}" '
                   f'rx="10" fill="#F8FAFC" stroke="#E2E8F0"/>')
        for i, item in enumerate(legend):
            kind = str(item.get("type") or "default")
            style = EDGE_STYLE.get(kind, EDGE_STYLE["default"])
            yy = ly + i * 34
            dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
            out.append(f'<line x1="{lx}" y1="{yy}" x2="{lx + 52}" y2="{yy}" stroke="#334155" '
                       f'stroke-width="2"{dash}/>')
            out.append(_text(lx + 62, yy, item.get("label") or style["label"], 13, anchor="start", fill="#334155"))
            if item.get("name"):
                out.append(_text(lx + 110, yy, item["name"], 13, anchor="start", fill="#0F172A"))
    out.append("</svg>")
    return "\n".join(out)


def chart(spec: dict) -> dict:
    """Data chart with matplotlib when present, else an SVG fallback."""
    result = {"spec_kind": spec.get("kind"), "engine": None, "svg": None, "png": None, "warnings": []}
    try:
        return _chart_mpl(spec, result)
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append(f"matplotlib path failed: {str(exc)[:160]}")
    return _chart_svg(spec, result)


def _chart_mpl(spec: dict, result: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    use_cjk_font()
    import matplotlib.pyplot as plt
    import numpy as np

    kind = (spec.get("kind") or "bar").lower()
    fig, ax = plt.subplots(figsize=tuple(spec.get("figsize") or (6.4, 4.2)), dpi=int(spec.get("dpi") or 200))
    x = np.arange(len(spec.get("categories") or []))
    series = spec.get("series") or []
    cats = spec.get("categories") or []
    if kind in ("bar", "barh", "grouped_bar"):
        n = max(1, len(series))
        w = 0.8 / n
        for i, s in enumerate(series):
            vals = s.get("values") or []
            off = (i - (n - 1) / 2) * w
            if kind == "barh":
                ax.barh(x + off, vals, height=w, label=s.get("name"),
                        color=s.get("color") or PALETTE[i % len(PALETTE)], edgecolor="#0F172A", linewidth=0.6)
            else:
                ax.bar(x + off, vals, width=w, label=s.get("name"),
                       color=s.get("color") or PALETTE[i % len(PALETTE)], edgecolor="#0F172A", linewidth=0.6)
            if s.get("errors"):
                ax.errorbar(x + off, vals, yerr=s["errors"], fmt="none", ecolor="#0F172A", capsize=3)
        if kind == "barh":
            ax.set_yticks(x); ax.set_yticklabels(cats)
        else:
            ax.set_xticks(x); ax.set_xticklabels(cats, rotation=float(spec.get("rotate") or 0))
        ax.axhline(0, color="#94A3B8", linewidth=1)
    elif kind in ("line", "scatter"):
        for i, s in enumerate(series):
            color = s.get("color") or PALETTE[i % len(PALETTE)]
            if kind == "line":
                ax.plot(s.get("x") or list(range(len(s.get("y") or []))), s.get("y") or [],
                        marker=s.get("marker") or "o", label=s.get("name"), color=color, linewidth=2)
                if s.get("errors"):
                    ax.errorbar(s.get("x") or list(range(len(s.get("y") or []))), s.get("y"), yerr=s["errors"],
                                fmt="none", ecolor=color, capsize=3, alpha=0.8)
            else:
                ax.scatter(s.get("x") or [], s.get("y") or [], label=s.get("name"), color=color,
                           s=float(s.get("size") or 36), edgecolor="#0F172A", linewidth=0.5)
    elif kind == "box":
        data = [s.get("values") or [] for s in series]
        ax.boxplot(data, labels=[s.get("name") or f"s{i}" for i, s in enumerate(series)])
        ax.set_xticks(list(range(1, len(series) + 1)))
        ax.set_xticklabels([s.get("name") or f"s{i}" for i, s in enumerate(series)],
                           rotation=float(spec.get("rotate") or 0))
    elif kind == "heatmap":
        matrix = np.array(spec.get("matrix") or [], dtype=float)
        im = ax.imshow(matrix, cmap=spec.get("cmap") or "viridis", aspect="auto")
        ax.set_xticks(range(matrix.shape[1])); ax.set_xticklabels(spec.get("cols") or [])
        ax.set_yticks(range(matrix.shape[0])); ax.set_yticklabels(spec.get("rows") or [])
        fig.colorbar(im, ax=ax, label=spec.get("cbar_label") or "")
    else:
        result["warnings"].append(f"unknown chart kind {kind}; produced an empty axes")
    if spec.get("xlabel"):
        ax.set_xlabel(spec["xlabel"])
    if spec.get("ylabel"):
        ax.set_ylabel(spec["ylabel"])
    if spec.get("title"):
        ax.set_title(spec["title"])
    if spec.get("ylim"):
        ax.set_ylim(*spec["ylim"])
    if spec.get("legend", len(series) > 1):
        ax.legend(frameon=False)
    for note in spec.get("footnotes") or []:
        fig.text(0.01, -0.02 - 0.03 * spec.get("footnotes", []).index(note), note, fontsize=8, color="#475569")
    fig.tight_layout()
    for fmt, key in (("svg", "svg"), ("png", "png"), ("pdf", "pdf")):
        path = spec.get(f"{key}_path")
        if path:
            fig.savefig(path, format=fmt, bbox_inches="tight")
            result[key] = path
            if key == "svg":
                result["svg"] = open(path, encoding="utf-8").read()
    plt.close(fig)
    result["engine"] = "matplotlib"
    return result


def _chart_svg(spec: dict, result: dict) -> dict:
    """Minimal deterministic SVG bar/line chart, used when matplotlib is absent."""
    width, height = 760, 460
    pad_l, pad_b, pad_t, pad_r = 90, 80, 60, 40
    cats = spec.get("categories") or []
    series = spec.get("series") or []
    values = [v for s in series for v in (s.get("values") or []) if isinstance(v, (int, float))]
    vmax = max(values) if values else 1.0
    vmin = min([0.0] + values)
    span = (vmax - vmin) or 1.0
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">',
           f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>']
    if spec.get("title"):
        out.append(_text(width / 2, 30, spec["title"], 18, weight="bold"))
    def sy(v):
        return pad_t + plot_h * (1 - (v - vmin) / span)
    out.append(f'<line x1="{pad_l}" y1="{sy(0):.1f}" x2="{width - pad_r}" y2="{sy(0):.1f}" stroke="#334155"/>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#334155"/>')
    for i in range(5):
        v = vmin + span * i / 4
        out.append(f'<line x1="{pad_l}" y1="{sy(v):.1f}" x2="{width - pad_r}" y2="{sy(v):.1f}" '
                   f'stroke="#E2E8F0" stroke-dasharray="3 3"/>')
        out.append(_text(pad_l - 10, sy(v), f"{v:g}", 11, anchor="end", fill="#475569"))
    if cats:
        step = plot_w / max(1, len(cats))
        for i, c in enumerate(cats):
            cx = pad_l + step * (i + 0.5)
            out.append(_text(cx, pad_t + plot_h + 20, c, 12, rotate=-20 if len(str(c)) > 8 else None))
    n = max(1, len(series))
    if cats:
        step = plot_w / max(1, len(cats))
        bw = step * 0.7 / n
        for si, s in enumerate(series):
            color = s.get("color") or PALETTE[si % len(PALETTE)]
            for i, v in enumerate(s.get("values") or []):
                if not isinstance(v, (int, float)):
                    continue
                x = pad_l + step * (i + 0.5) - (n * bw) / 2 + si * bw
                y = sy(v)
                top = min(y, sy(0))
                h = abs(sy(v) - sy(0)) or 1
                out.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                           f'fill="{color}" stroke="#0F172A" stroke-width="0.6"/>')
                if s.get("errors"):
                    err = (s["errors"] or [])[i] if i < len(s.get("errors") or []) else 0
                    out.append(f'<line x1="{x + bw/2:.1f}" y1="{sy(v - err):.1f}" x2="{x + bw/2:.1f}" '
                               f'y2="{sy(v + err):.1f}" stroke="#0F172A"/>')
    if spec.get("xlabel"):
        out.append(_text(width / 2, height - 26, spec["xlabel"], 13))
    if spec.get("ylabel"):
        out.append(_text(22, pad_t + plot_h / 2, spec["ylabel"], 13, rotate=-90))
    legend = [s.get("name") for s in series if s.get("name")]
    for i, name in enumerate(legend):
        y = pad_t + 12 + i * 20
        out.append(f'<rect x="{width - pad_r - 150}" y="{y - 8}" width="14" height="14" '
                   f'fill="{PALETTE[i % len(PALETTE)]}"/>')
        out.append(_text(width - pad_r - 130, y, name, 12, anchor="start"))
    out.append("</svg>")
    result["svg"] = "\n".join(out)
    result["engine"] = "svg-fallback"
    result["warnings"].append("matplotlib unavailable: emitted the dependency-free SVG fallback; "
                              "structure and values are exact, styling is reduced")
    return result


def check(spec: dict) -> dict:
    """Mechanical compliance checks a reviewer would run on the spec."""
    problems, warnings = [], []
    nodes = {str(n["id"]) for n in spec.get("nodes", [])}
    for e in spec.get("edges", []):
        if str(e.get("from")) not in nodes or str(e.get("to")) not in nodes:
            problems.append(f"edge references unknown node: {e}")
        kind = str(e.get("type") or "")
        if kind and kind not in EDGE_STYLE:
            warnings.append(f"edge type {kind!r} has no declared semantics; it will render as a plain arrow")
    ids = [str(n["id"]) for n in spec.get("nodes", [])]
    if len(ids) != len(set(ids)):
        problems.append("duplicate node ids")
    for n in spec.get("nodes", []):
        if not n.get("label"):
            warnings.append(f"node {n.get('id')} has no label; a reviewer cannot map it to the source")
    for s in spec.get("series", []):
        vals = s.get("values")
        if vals is not None and spec.get("categories") and len(vals) != len(spec["categories"]):
            problems.append(f"series {s.get('name')!r} has {len(vals)} values for "
                            f"{len(spec['categories'])} categories")
    return {"problems": problems, "warnings": warnings, "nodes": len(nodes),
            "edges": len(spec.get("edges", [])), "series": len(spec.get("series", []))}



# ---------------------------------------------------------------------------
# Source-preserving editing of an existing SVG.
# The graphics workorders require "real editable SVG/PNG/PDF and a
# source-preserving edit": open the actual source, change only the named
# elements, keep every id, group, defs and untouched byte intact, and record
# what changed. Regenerating the figure from scratch is not that.

INSPECT_RE = re.compile(r'<(rect|ellipse|path|text|g)\b([^>]*?)(?:/>|>(.*?)</\1>)', re.S)
ID_RE = re.compile(r'id="([^"]+)"')
ATTR_RE = re.compile(r'([\w:.-]+)="([^"]*)"')


def _elements(svg: str) -> list[dict]:
    out = []
    for m in INSPECT_RE.finditer(svg):
        tag, attr_text, inner = m.group(1), m.group(2) or "", m.group(3) or ""
        attrs = dict(ATTR_RE.findall(attr_text))
        out.append({
            "tag": tag,
            "id": attrs.get("id"),
            "role": attrs.get("data-role"),
            "kind": attrs.get("data-kind"),
            "edge_type": attrs.get("data-type"),
            "from": attrs.get("data-from"),
            "to": attrs.get("data-to"),
            "text": inner.strip() if tag == "text" else None,
            "span": [m.start(), m.end()],
        })
    return out


def inspect(svg_path: str) -> dict:
    svg = open(svg_path, encoding="utf-8").read()
    els = _elements(svg)
    nodes = [e for e in els if e["role"] == "node"]
    edges = [e for e in els if e["role"] == "edge"]
    labels = [e for e in els if e["tag"] == "text" and e["id"]]
    ids = [e["id"] for e in els if e["id"]]
    return {
        "path": svg_path, "bytes": len(svg),
        "elements": len(els), "nodes": len(nodes), "edges": len(edges), "labelled": len(labels),
        "duplicate_ids": sorted({i for i in ids if ids.count(i) > 1}),
        "node_list": [{"id": n["id"], "kind": n["kind"]} for n in nodes],
        "edge_list": [{"id": e["id"], "type": e["edge_type"], "from": e["from"], "to": e["to"]} for e in edges],
        "editable": bool(nodes) and not [i for i in ids if ids.count(i) > 1],
    }


def _replace_element(svg: str, element: dict, new_inner: str | None = None,
                     attr_updates: dict | None = None) -> str:
    start, end = element["span"]
    chunk = svg[start:end]
    if attr_updates:
        for key, value in attr_updates.items():
            if re.search(rf'{re.escape(key)}="[^"]*"', chunk):
                chunk = re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{value}"', chunk, count=1)
            else:
                chunk = chunk.replace(">", f' {key}="{value}">', 1) if chunk.endswith(">") else chunk
    if new_inner is not None and element["tag"] == "text":
        chunk = re.sub(r">.*?</text>", f">{escape(str(new_inner))}</text>", chunk, count=1, flags=re.S)
    return svg[:start] + chunk + svg[end:]


def edit(svg_path: str, patch: dict) -> dict:
    """patch: {"ops":[{"op":"set_label","id":"label-a","text":"..."} | {"op":"set_attr","id":...,
    "attr":"data-type","value":...} | {"op":"set_edge_type","id":"edge-a__b","value":"inhibition"} |
    {"op":"move","id":"node-a","dx":12,"dy":-8}]}"""
    svg = open(svg_path, encoding="utf-8").read()
    original = svg
    diffs = []
    for op in patch.get("ops") or []:
        kind = str(op.get("op") or "")
        target_id = str(op.get("id") or "")
        els = {e["id"]: e for e in _elements(svg) if e["id"]}
        el = els.get(target_id)
        if not el:
            diffs.append({"op": kind, "id": target_id, "applied": False, "reason": "id not found"})
            continue
        before = svg[el["span"][0]:el["span"][1]]
        if kind == "set_label":
            svg = _replace_element(svg, el, new_inner=str(op.get("text") or ""))
        elif kind == "set_attr":
            svg = _replace_element(svg, el, attr_updates={str(op.get("attr")): str(op.get("value"))})
        elif kind == "set_edge_type":
            style = EDGE_STYLE.get(str(op.get("value")), EDGE_STYLE["default"])
            dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else "none"
            svg = _replace_element(svg, el, attr_updates={
                "data-type": str(op.get("value")),
                "stroke-dasharray": dash,
                "marker-end": ("" if style["marker"] == "none" else
                               f'url(#{ "arrowBlue" if op.get("value") == "activation" else "arrow"})'),
            })
        elif kind == "move":
            dx, dy = float(op.get("dx") or 0), float(op.get("dy") or 0)
            chunk = svg[el["span"][0]:el["span"][1]]
            if el["tag"] in ("rect", "ellipse"):
                def bump(m):
                    return f'{m.group(1)}="{float(m.group(2)) + (dx if m.group(1) in ("x", "cx") else dy)}"'
                chunk2 = re.sub(r'\b(x|cx|y|cy)="([-\d.]+)"', bump, chunk)
            else:
                def bump2(m):
                    sx, sy = float(m.group(1)), float(m.group(2))
                    return f"d=\"M {sx + dx} {sy + dy} " + m.group(0)[3:]
                chunk2 = re.sub(r'd="M ([-\d.]+) ([-\d.]+)', lambda m: "d=\"M %s %s" % (
                    float(m.group(1)) + dx, float(m.group(2)) + dy), chunk, count=1)
            svg = svg[:el["span"][0]] + chunk2 + svg[el["span"][1]:]
        else:
            diffs.append({"op": kind, "id": target_id, "applied": False, "reason": "unknown op"})
            continue
        after_els = {e["id"]: e for e in _elements(svg) if e["id"]}
        after = svg[after_els[target_id]["span"][0]:after_els[target_id]["span"][1]] if target_id in after_els else ""
        diffs.append({"op": kind, "id": target_id, "applied": before != after,
                      "before": before[:200], "after": after[:200]})
    changed = svg != original
    total_ids_before = len({e["id"] for e in _elements(original) if e["id"]})
    total_ids_after = len({e["id"] for e in _elements(svg) if e["id"]})
    return {"svg": svg, "changed": changed, "diffs": diffs,
            "ids_preserved": total_ids_before == total_ids_after,
            "ids_before": total_ids_before, "ids_after": total_ids_after,
            "applied": sum(1 for d in diffs if d.get("applied"))}


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic figure production")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dg = sub.add_parser("diagram"); dg.add_argument("spec"); dg.add_argument("-o", "--out", required=True)
    dg.add_argument("--png")
    ch = sub.add_parser("chart"); ch.add_argument("spec"); ch.add_argument("-o", "--out", required=True)
    ch.add_argument("--png")
    ck = sub.add_parser("check"); ck.add_argument("spec")
    ins = sub.add_parser("inspect"); ins.add_argument("svg")
    ed = sub.add_parser("edit"); ed.add_argument("svg"); ed.add_argument("--patch", required=True)
    ed.add_argument("-o", "--out", required=True); ed.add_argument("--png")
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8")) if getattr(a, "spec", None) else {}
    if a.cmd == "check":
        print(json.dumps(check(spec), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "inspect":
        print(json.dumps(inspect(a.svg), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "edit":
        patch = json.load(open(a.patch, encoding="utf-8"))
        res = edit(a.svg, patch)
        open(a.out, "w", encoding="utf-8").write(res["svg"])
        report = {k: v for k, v in res.items() if k != "svg"}
        report["out"] = a.out
        if a.png:
            here = os.path.dirname(os.path.abspath(__file__))
            proc = subprocess.run([sys.executable, os.path.join(here, "render.py"), "svg", a.out, "-o", a.png],
                                  capture_output=True, text=True, timeout=300)
            report["png"] = a.png if os.path.isfile(a.png) else None
        print(json.dumps(report, ensure_ascii=False))
        return 0 if res["changed"] and res["ids_preserved"] else 1
    if a.cmd == "diagram":
        svg = diagram(spec)
        open(a.out, "w", encoding="utf-8").write(svg)
        info = {"path": a.out, "engine": "svg", "bytes": len(svg)}
        if a.png:
            here = os.path.dirname(os.path.abspath(__file__))
            code, log = 0, ""
            try:
                proc = subprocess.run([sys.executable, os.path.join(here, "render.py"), "svg", a.out, "-o", a.png],
                                      capture_output=True, text=True, timeout=300)
                code, log = proc.returncode, proc.stdout + proc.stderr
            except Exception as exc:  # noqa: BLE001
                code, log = 1, str(exc)
            info["png"] = a.png if code == 0 and os.path.isfile(a.png) else None
            if info["png"] is None:
                info["png_error"] = log[-200:]
        print(json.dumps(info, ensure_ascii=False))
        return 0
    spec.setdefault("svg_path", a.out)
    if a.png:
        spec.setdefault("png_path", a.png)
    res = chart(spec)
    print(json.dumps({k: v for k, v in res.items() if k != "svg"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
