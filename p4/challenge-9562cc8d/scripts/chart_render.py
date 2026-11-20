#!/usr/bin/env python3
"""chart_render — plot the table that was supplied, and prove each mark came from a cell.

A data-visualisation deliverable is checked on two things: the chart exists in an editable format, and every
bar/point sits at exactly the value in the source table. So the renderer never smooths, never reorders by
value (source order is kept), and writes `chart_data.tsv` listing the number behind each mark — that file is
what makes the picture checkable.

  chart type is chosen from the data shape, not from taste:
    1 categorical column + ≥1 numeric  -> bar (grouped when several numeric columns)
    a numeric or date-like x + numeric -> line
    exactly two numeric columns        -> scatter
  outputs: chart.svg (+ chart.png / chart.pdf through PyMuPDF when available), chart_data.tsv,
           chart_spec.json, check_report.json, result.json, inventory.tsv, repro_manifest.json

usage:
  chart_render.py --input /input --output /app/output [--table path/to.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _meta import attach as meta_attach
    from _render import maybe_svg_outputs
except Exception:                                  # the engine must still run without the helpers
    def meta_attach(inp, out):
        return {"unavailable": True}

    def maybe_svg_outputs(svg, out, stem="chart"):
        return {"png": None, "pdf": None, "reason": "helpers unavailable"}

NUMRX = re.compile(r"^-?\d+(?:\.\d+)?$")
SKIP = ("output-contract", "instruction", "skill", "readme", "case.json", "resources.yaml")
PALETTE = ["#1f6feb", "#e07b39", "#2e8b57", "#8a4fd1", "#c2410c", "#0e7490"]


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def find_tables(inp: Path) -> list[Path]:
    out = []
    for p in sorted(inp.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".csv", ".tsv"} \
                and not any(k in p.name.lower() for k in SKIP):
            out.append(p)
    return out


def read_table(p: Path) -> tuple[list[str], list[list[str]]]:
    delim = "\t" if p.suffix.lower() == ".tsv" else ","
    with p.open(encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=delim))
    if not rows:
        return [], []
    header = [norm(h) for h in rows[0]]
    body = [[norm(c) for c in r] for r in rows[1:] if any(norm(c) for c in r)]
    return header, body


def classify(header: list[str], body: list[list[str]]) -> dict:
    cols: dict[str, list] = {}
    for j, name in enumerate(header):
        vals = [(r[j] if j < len(r) else "") for r in body]
        numeric = [v for v in vals if NUMRX.match(v.replace("%", "").replace(",", ""))]
        cols[name] = {"values": vals, "numeric": len(numeric) >= max(2, 0.8 * len(vals)),
                      "ratio": round(len(numeric) / max(1, len(vals)), 2)}
    num_cols = [c for c, m in cols.items() if m["numeric"]]
    cat_cols = [c for c, m in cols.items() if not m["numeric"]]
    x_like = None
    if num_cols and cat_cols:
        kind = "bar"
        x_like = cat_cols[0]
        series = num_cols
    elif len(num_cols) >= 2:
        first = cols[num_cols[0]]["values"]
        steps = 0
        for a, b in zip(first, first[1:]):
            try:
                steps += 1 if float(a) <= float(b) else 0
            except Exception:
                pass
        kind = "line" if steps >= len(first) - 2 else "scatter"
        x_like = num_cols[0]
        series = num_cols[1:] if kind != "scatter" else [num_cols[1]]
    elif num_cols:
        kind, x_like, series = "bar", cat_cols[0] if cat_cols else None, num_cols
    else:
        return {"kind": None, "reason": "表里没有数值列，画不了数据图"}
    return {"kind": kind, "x": x_like, "series": series[:4], "cols": cols}


def svg_chart(header: list[str], body: list[list[str]], plan: dict, title: str) -> tuple[str, list[dict]]:
    w, h = 900, 460
    pad_l, pad_r, pad_t, pad_b = 90, 40, 70, 80
    cols = plan["cols"]
    kind, xname = plan["kind"], plan["x"]
    xs = cols[xname]["values"] if xname else [str(i + 1) for i in range(len(body))]
    series = plan["series"]
    data_all = []
    for sname in series:
        vals = []
        for v in cols[sname]["values"]:
            try:
                vals.append(float(v.replace("%", "").replace(",", "")))
            except Exception:
                vals.append(None)
        data_all.append((sname, vals))
    flat = [v for _, vs in data_all for v in vs if v is not None]
    vmin, vmax = (min(flat), max(flat)) if flat else (0.0, 1.0)
    if vmax == vmin:
        vmax = vmin + 1
    span = vmax - vmin
    vmin, vmax = vmin - 0.08 * span, vmax + 0.08 * span
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b

    def xpos(i: int, n: int) -> float:
        if kind == "bar":
            step = plot_w / max(1, n)
            return pad_l + step * (i + 0.5)
        return pad_l + (plot_w * i / max(1, n - 1))

    def ypos(v: float) -> float:
        return pad_t + plot_h - (v - vmin) / (vmax - vmin) * plot_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
           f'font-family="Helvetica, Arial, sans-serif">', f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           f'<text x="{w / 2}" y="36" font-size="18" text-anchor="middle" font-weight="bold">'
           f'{title}</text>']
    # axes + ticks
    for k in range(6):
        v = vmin + (vmax - vmin) * k / 5
        y = ypos(v)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" font-size="10" text-anchor="end" '
                   f'fill="#4b5563">{v:.3g}</text>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#9ca3af"/>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{w - pad_r}" y2="{pad_t + plot_h}" '
               f'stroke="#9ca3af"/>')
    n = max(1, len(xs))
    marks: list[dict] = []
    if kind == "bar":
        group = plot_w / n
        bw = group * 0.8 / max(1, len(series))
        for i, xv in enumerate(xs):
            for si, (sname, vals) in enumerate(data_all):
                v = vals[i] if i < len(vals) else None
                if v is None:
                    continue
                x = pad_l + group * i + group * 0.1 + bw * si
                y = ypos(v)
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                           f'height="{pad_t + plot_h - y:.1f}" fill="{PALETTE[si % len(PALETTE)]}"/>')
                marks.append({"x": xv, "series": sname, "value": v, "x_px": round(x, 1),
                              "y_px": round(y, 1)})
            out.append(f'<text x="{pad_l + group * i + group / 2:.1f}" y="{pad_t + plot_h + 18}" '
                       f'font-size="10" text-anchor="middle" fill="#374151">{xv[:14]}</text>')
    else:
        for si, (sname, vals) in enumerate(data_all):
            pts = [(xpos(i, n), ypos(v)) for i, v in enumerate(vals) if v is not None]
            if not pts:
                continue
            out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
                       f'" fill="none" stroke="{PALETTE[si % len(PALETTE)]}" stroke-width="2"/>')
            for i, v in enumerate(vals):
                if v is None:
                    continue
                cx, cy = xpos(i, n), ypos(v)
                out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.2" '
                           f'fill="{PALETTE[si % len(PALETTE)]}"/>')
                marks.append({"x": xs[i] if i < len(xs) else str(i), "series": sname, "value": v,
                              "x_px": round(cx, 1), "y_px": round(cy, 1)})
        for i, xv in enumerate(xs):
            if len(xs) <= 12 or i % max(1, len(xs) // 10) == 0:
                out.append(f'<text x="{xpos(i, n):.1f}" y="{pad_t + plot_h + 18}" font-size="10" '
                           f'text-anchor="middle" fill="#374151">{xv[:10]}</text>')
    out.append(f'<text x="{w / 2}" y="{h - 34}" font-size="12" text-anchor="middle" fill="#111827">'
               f'{xname or "index"}</text>')
    out.append(f'<text x="24" y="{pad_t + plot_h / 2}" font-size="12" text-anchor="middle" '
               f'fill="#111827" transform="rotate(-90 24 {pad_t + plot_h / 2:.1f})">'
               f'{", ".join(series)}</text>')
    for si, sname in enumerate(series):
        out.append(f'<rect x="{w - pad_r - 150}" y="{pad_t + 6 + si * 16}" width="10" height="10" '
                   f'fill="{PALETTE[si % len(PALETTE)]}"/>')
        out.append(f'<text x="{w - pad_r - 134}" y="{pad_t + 15 + si * 16}" font-size="10" '
                   f'fill="#374151">{sname}</text>')
    out.append("</svg>")
    return "".join(out), marks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--table", default=None)
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    tables = [Path(args.table)] if args.table else find_tables(inp)
    chosen, header, body, plan = None, [], [], {}
    for p in tables:
        h, b = read_table(p)
        if len(b) < 2 or len(h) < 2:
            continue
        pl = classify(h, b)
        if pl.get("kind"):
            chosen, header, body, plan = p, h, b, pl
            break
    if chosen is None:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["输入里没有可画的数值表（需要 CSV/TSV 且含 ≥2 行数值）"],
             "verification": {"all_passed": False}}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    title = chosen.stem
    svg, marks = svg_chart(header, body, plan, title)
    (out / "chart.svg").write_text(svg, encoding="utf-8")
    with (out / "chart_data.tsv").open("w", encoding="utf-8") as fh:
        fh.write("x\tseries\tvalue\tx_px\ty_px\n")
        for m in marks:
            fh.write(f'{m["x"]}\t{m["series"]}\t{m["value"]}\t{m["x_px"]}\t{m["y_px"]}\n')
    spec = {"schema_version": "chart-spec/v1", "kind": plan["kind"], "x": plan["x"],
            "series": plan["series"], "source_table": chosen.name, "rows": len(body)}
    (out / "chart_spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    rinfo = maybe_svg_outputs(out / "chart.svg", out, "chart")
    # every mark must equal a source cell (float comparison on the same column)
    src_vals = set()
    for sname in plan["series"]:
        for v in plan["cols"][sname]["values"]:
            try:
                src_vals.add(round(float(v.replace("%", "").replace(",", "")), 6))
            except Exception:
                continue
    checks = {
        "chart_written": len(svg) > 800,
        "every_mark_matches_a_source_cell": all(round(m["value"], 6) in src_vals for m in marks),
        "category_order_preserved": plan["kind"] != "bar" or
        [m["x"] for m in marks if m["series"] == plan["series"][0]]
        == [v for v in plan["cols"][plan["x"]]["values"]][:len([m for m in marks if m["series"] == plan["series"][0]])],
        "axis_titles_present": f'>{plan["x"]}<' in svg and any(f">{s}<" in svg for s in plan["series"]),
        "values_listed_for_recheck": (out / "chart_data.tsv").stat().st_size > 40,
    }
    checks["all_passed"] = all(checks.values())
    (out / "check_report.json").write_text(json.dumps(
        {"case_id": case_id, "checks": checks, "kind": plan["kind"], "marks": len(marks),
         "render": rinfo}, ensure_ascii=False, indent=1), encoding="utf-8")
    gaps = []
    if not rinfo.get("png"):
        gaps.append("没有可用的 SVG 渲染器（fitz/rsvg/cairosvg），未产出 PNG/PDF；SVG 本身可编辑")
    if plan["kind"] == "bar" and len(body) > 20:
        gaps.append(f"类别数 {len(body)} 较多，横轴标签只显示前 14 个字符")
    payload = {"case_id": case_id, "status": "answered",
               "chart": {"primary": "chart.svg", "png": rinfo.get("png"), "pdf": rinfo.get("pdf"),
                         "kind": plan["kind"], "x": plan["x"], "series": plan["series"],
                         "marks": len(marks), "source_table": chosen.name},
               "checks": "check_report.json", "data": "chart_data.tsv",
               "gaps": gaps,
               "limitations": ["图由源表直接绘制：不做插值、不平滑、不按数值重排类别",
                               "每个标记的数值都列在 chart_data.tsv，可逐点复核",
                               "PNG/PDF 依赖渲染器，缺失时只产出可编辑 SVG"],
               "verification": checks, "render": rinfo}
    payload["metadata_coverage"] = meta_attach(inp, out)
    # 链式调用时保留前一步（清洗）的 result.json，本步另写 chart_result.json
    _target = out / ("chart_result.json" if (out / "result.json").is_file() else "result.json")
    _target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": "answered", "kind": plan["kind"],
                      "marks": len(marks), "png": bool(rinfo.get("png")),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
