#!/usr/bin/env python3
"""Parse a raster figure (PNG/JPEG/TIFF) into structure plus calibrated data.

This is the module the rest of the skill was missing: everything else in this
package reads PDF text or *produces* figures. A scientific case hands over one
real image, and the honest work on that image is:

  1. decide what kind of image it actually is - chart with axes, photograph,
     schematic/diagram, or table image - instead of assuming a chart;
  2. locate the axis frame and the tick marks in pixels;
  3. turn pixel rows/columns into data coordinates (linear or log, decided by
     which fit the ticks actually support);
  4. digitise a series by colour, and measure error bars where they exist.

Division of labour, stated up front because it is the honest boundary:
* no OCR engine is required or assumed. Tick *positions* are found from pixels;
  tick *values* are supplied by the reader (the agent can see the image) and
  are recorded verbatim with the pixel they were read at. If a value cannot be
  read, pass null and it stays `not_reported` - it is never invented here.
* the numerical part (calibration fit, residuals, digitisation, error-bar
  length) is done deterministically on pixels, so it can be re-run and audited.

Coordinate conventions
* origin is the image's top-left, x to the right, y downwards (pixels);
* data y therefore decreases as pixel y increases, and the fit handles it.

Locators emitted (used as `evidence.locator` in the result):
  img:<file>@<x>,<y>          one pixel, for a digitised or annotated point
  img:<file>#frame<l,t,r,b>   the detected plot box
  img:<file>#xtick<px>        one tick mark found on the x axis
  img:<file>#line<l,t,r,b>    a long straight run treated as an axis line

CLI:
  python3 chart_parse.py probe    IMAGE [-o out.json]
  python3 chart_parse.py calibrate SPEC.json [-o calib.json]
  python3 chart_parse.py digitize IMAGE --calib calib.json ... [-o series.json]
  python3 chart_parse.py run      SPEC.json [-o out.json]
  python3 chart_parse.py version
"""
from __future__ import annotations

import argparse
import json
import os
import sys

VERSION = "1.1.0"

try:
    import numpy as np
except Exception:  # pragma: no cover - reported to the caller, never silent
    np = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

DARK = 110          # luminance below this counts as ink for axis/tick detection
NEAR_WHITE = 245    # luminance above this counts as paper


# ---------------------------------------------------------------- loading

def load(path):
    """Return an (H, W, 3) uint8 RGB array. Raises with a readable message."""
    if Image is None:
        raise RuntimeError("Pillow is not importable; chart_parse.py needs PIL.Image")
    if np is None:
        raise RuntimeError("numpy is not importable; chart_parse.py needs numpy")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    im = Image.open(path)
    fmt, mode, size = im.format, im.mode, im.size
    if mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    return rgb, {"file": os.path.basename(path), "path": path, "format": fmt,
                 "mode": mode, "width": size[0], "height": size[1]}


def luma(rgb):
    r = rgb.astype(np.float32)
    return (0.299 * r[:, :, 0] + 0.587 * r[:, :, 1] + 0.114 * r[:, :, 2])


# ---------------------------------------------------------------- geometry

def runs(mask_1d, min_len):
    """[(start, end_exclusive)] of True runs at least min_len long."""
    out, start = [], None
    for i, v in enumerate(mask_1d):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(mask_1d) - start >= min_len:
        out.append((start, len(mask_1d)))
    return out


def detect_lines(dark):
    """Long horizontal and vertical ink runs - axis candidates.

    A row/column is scanned for runs of dark pixels at least `min_len` long,
    where min_len is a fraction of the image's short side. Real plot axes are
    long and straight; text and organic photograph content are not.
    """
    h, w = dark.shape
    short = min(h, w)
    hmin = max(30, int(0.30 * w))
    vmin = max(30, int(0.30 * h))
    horiz, vert = [], []
    for y in range(h):
        for s, e in runs(dark[y], hmin):
            horiz.append({"y": y, "x0": s, "x1": e, "len": e - s})
    for x in range(w):
        for s, e in runs(dark[:, x], vmin):
            vert.append({"x": x, "y0": s, "y1": e, "len": e - s})
    # merge adjacent scanlines belonging to the same thick line
    horiz = _merge(horiz, "y", "x0", "x1")
    vert = _merge(vert, "x", "y0", "y1")
    return horiz, vert


def _merge(items, key, a, b):
    items.sort(key=lambda d: (d[a], d[key]))
    merged = []
    for it in items:
        for m in merged:
            if abs(m[key] - it[key]) <= 2 and abs(m[a] - it[a]) <= 3 and abs(m[b] - it[b]) <= 3:
                m.setdefault("_n", 1)
                m["_n"] += 1
                m[key] = (m[key] * (m["_n"] - 1) + it[key]) / m["_n"]
                break
        else:
            merged.append(dict(it))
    for m in merged:
        m[key] = int(round(m[key]))
        m.pop("_n", None)
    merged.sort(key=lambda d: -d["len"])
    return merged


def ink_profile(dark, box):
    """Dark-pixel counts per column and per row inside a region."""
    l, t, r, b = box
    sub = dark[t:b, l:r]
    return sub.sum(axis=0), sub.sum(axis=1)


def find_ticks(dark, axis, pos, span, side, max_depth=14):
    """Tick marks just outside an axis line -> positions, split major/minor.

    A tick is a one-pixel-wide stroke protruding from the axis, so a single-row
    scan sees a run of length 1 and cannot tell it from glyph ink. Adjacency is
    what separates them: a tick is dark in the rows immediately next to the
    axis, while tick labels start a visible gap further out.

    Once candidates are taken from that adjacent band, their *length* (how many
    consecutive depths stay dark) separates major from minor ticks. Minor ticks
    must not be discarded - on a log axis the minors are the only proof that the
    scale is logarithmic, since only the decades are labelled.
    """
    limit = dark.shape[1] if axis == "x" else dark.shape[0]
    a = max(0, int(span[0]))
    b = min(int(span[1]), limit)
    if b - a < 4:
        return []

    def scan(d):
        """Indices that are dark at depth `d` from the axis, or None if off-image."""
        if axis == "x":
            y = pos + d if side == "below" else pos - d
            if not (0 <= y < dark.shape[0]):
                return None
            return {a + i for i in np.nonzero(dark[y, a:b])[0]}
        x = pos + d if side == "right" else pos - d
        if not (0 <= x < dark.shape[1]):
            return None
        return {a + i for i in np.nonzero(dark[a:b, x])[0]}

    near = scan(2) or set()
    near = near | (scan(1) or set())
    if not near:
        return []

    # how far each candidate extends away from the axis, in whole pixels
    length = {p: 1 for p in near}
    prev = near
    for d in range(3, max_depth + 1):
        cur = scan(d)
        if cur is None:
            break
        alive = prev & cur
        for p in alive:
            length[p] = d - 1
        if not alive:
            break
        prev = alive

    ordered = sorted(near)
    groups, cur = [], [ordered[0]]
    for p in ordered[1:]:
        if p - cur[-1] <= 1:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)

    ticks = [{"px": round(sum(g) / len(g), 1), "width_px": len(g),
              "length_px": max(length[p] for p in g)} for g in groups]
    if ticks:
        longest = max(t["length_px"] for t in ticks)
        for t in ticks:
            t["major"] = t["length_px"] >= longest - 1
    return ticks


def _bands(pos, side, limit, depth=8):
    """One-pixel bands marching away from the axis line, nearest first."""
    for d in range(1, depth + 1):
        if side in ("below", "right"):
            s, e = pos + d, pos + d + 1
        else:
            s, e = pos - d - 1, pos - d
        if s >= 0 and e <= limit:
            yield (s, e)


# ---------------------------------------------------------------- scale fits

def fit_scale(ticks_px, values):
    """Fit pixel = a + b*f(value) for f in {identity, log10}.

    Returns both fits so the choice is auditable, plus a verdict. The verdict
    prefers linear unless log is clearly better AND the values are all > 0.
    """
    if len(ticks_px) != len(values):
        raise ValueError(f"{len(ticks_px)} tick pixels vs {len(values)} values")
    pairs = [(float(p), float(v)) for p, v in zip(ticks_px, values) if v is not None]
    if len(pairs) < 2:
        raise ValueError("need at least 2 ticks with a known value to calibrate")
    px = np.array([p for p, _ in pairs], dtype=float)
    val = np.array([v for _, v in pairs], dtype=float)

    def _fit(target):
        A = np.vstack([np.ones_like(target), target]).T
        coef, *_ = np.linalg.lstsq(A, px, rcond=None)
        pred = A @ coef
        resid = px - pred
        ss_res = float((resid ** 2).sum())
        ss_tot = float(((px - px.mean()) ** 2).sum()) or 1e-12
        return {"a": float(coef[0]), "b": float(coef[1]),
                "r2": 1.0 - ss_res / ss_tot,
                "max_abs_residual_px": float(np.abs(resid).max()),
                "rmse_px": float(np.sqrt((resid ** 2).mean()))}

    lin = _fit(val)
    log = None
    if (val > 0).all():
        log = _fit(np.log10(val))

    verdict = "linear"
    reason = "only a linear fit is admissible (non-positive values present)"
    if log:
        lin_r, log_r = lin["max_abs_residual_px"], log["max_abs_residual_px"]
        # ticks evenly spaced in log space land on even pixel spacing
        if log_r <= 1.5 and lin_r > 3 * max(log_r, 0.5):
            verdict = "log10"
            reason = (f"log10 residuals max={log_r:.2f}px vs linear max={lin_r:.2f}px; "
                      f"even pixel spacing tracks equal ratios")
        else:
            reason = (f"linear residuals max={lin_r:.2f}px vs log10 max={log_r:.2f}px; "
                      f"log not clearly better")
    return {"verdict": verdict, "reason": reason, "linear": lin, "log10": log,
            "ticks_used": [{"px": p, "value": v} for p, v in pairs]}


def apply_scale(cal, pixels):
    """Map pixel coordinates to data values using a fitted calibration."""
    px = np.asarray(pixels, dtype=float)
    a, b = cal["a"], cal["b"]
    t = (px - a) / b
    if cal.get("scale") == "log10":
        return np.power(10.0, t)
    return t


def value_to_pixel(cal, values):
    v = np.asarray(values, dtype=float)
    t = np.log10(v) if cal.get("scale") == "log10" else v
    return cal["a"] + cal["b"] * t


# ---------------------------------------------------------------- series

def color_mask(rgb, color, tol):
    d = np.abs(rgb.astype(np.int16) - np.array(color, dtype=np.int16))
    # per-channel tolerance keeps distinct hues apart better than a sphere
    m = (d[:, :, 0] <= tol) & (d[:, :, 1] <= tol) & (d[:, :, 2] <= tol)
    # A per-channel box lets neutral grey through against any saturated colour:
    # the greys of antialiased black text are within `tol` of every channel of a
    # saturated series colour, so tick labels get read as data points. A
    # saturated target must therefore also match on chroma (max-min). Achromatic
    # targets and near-grey ones keep the box test only.
    tgt = np.asarray(color, dtype=np.int16)
    tgt_chroma = int(tgt.max() - tgt.min())
    if tgt_chroma >= 40:
        px_chroma = rgb.astype(np.int16).max(axis=2) - rgb.astype(np.int16).min(axis=2)
        m &= px_chroma >= max(12, int(0.35 * tgt_chroma))
    return m


def digitize_series(rgb, mask, box, min_run=1):
    """Per-column centroid of a colour mask inside the plot box.

    Returns points in pixel coordinates plus per-point vertical extent, which
    is where error-bar length later comes from. A column with no masked pixel
    ends the current segment: gaps are reported, never bridged.
    """
    l, t, r, b = box
    sub = mask[t:b, l:r]
    cols = []
    for x in range(sub.shape[1]):
        ys = np.nonzero(sub[:, x])[0]
        if ys.size < min_run:
            cols.append(None)
            continue
        cols.append((x + l, float(ys.mean()) + t, int(ys.min()) + t, int(ys.max()) + t, int(ys.size)))
    segments, cur = [], []
    for c in cols:
        if c is None:
            if cur:
                segments.append(cur)
                cur = []
            continue
        cur.append(c)
    if cur:
        segments.append(cur)
    return segments


def detect_error_bars(segment, thickness_px):
    """Columns whose vertical ink extent far exceeds the stroke -> error bar.

    A marker/line is ~`thickness_px` tall; an error bar adds a thin vertical
    whisker at one column. We report the run's half-extent so it converts to a
    data-space half-height with the local scale.
    """
    bars = []
    for x, ymid, ymin, ymax, n in segment:
        extent = ymax - ymin
        if extent >= max(6, thickness_px * 2.5):
            bars.append({"px": x, "top_px": ymin, "bottom_px": ymax,
                         "half_extent_px": extent / 2.0, "extent_px": extent})
    return bars


def stroke_thickness(segment):
    """Median per-column ink height: the line's own thickness in pixels."""
    if not segment:
        return 0.0
    heights = [ymax - ymin for _, _, ymin, ymax, _ in segment]
    return float(np.median(heights))


# ---------------------------------------------------------------- legend

def window_count(mask, k):
    """Number of True cells in every k x k window, via an integral image."""
    m = mask.astype(np.int32)
    integ = np.zeros((m.shape[0] + 1, m.shape[1] + 1), dtype=np.int64)
    integ[1:, 1:] = m.cumsum(0).cumsum(1)
    H, W = m.shape
    y = np.arange(H)
    x = np.arange(W)
    yk = np.minimum(y + k, H)
    xk = np.minimum(x + k, W)
    return (integ[np.ix_(yk, xk)] - integ[np.ix_(y, xk)]
            - integ[np.ix_(yk, x)] + integ[np.ix_(y, x)])


def components(mask, max_cells=200000):
    """Connected components (8-neighbour) of a boolean mask as bounding boxes.

    Returns dicts with bounds, pixel count and fill ratio. The fill ratio and
    the aspect ratio are what separate a legend handle (a short, densely filled
    dash) from a data curve (a long, sparse polyline) - not the position, which
    would just be a guess about house style.
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0 or xs.size > max_cells:
        return []
    ptset = set(zip(ys.tolist(), xs.tolist()))
    seen = set()
    out = []
    for p in list(ptset):
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            cy, cx = stack.pop()
            comp.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    q = (cy + dy, cx + dx)
                    if q in ptset and q not in seen:
                        seen.add(q)
                        stack.append(q)
        y0 = min(c[0] for c in comp)
        y1 = max(c[0] for c in comp)
        x0 = min(c[1] for c in comp)
        x1 = max(c[1] for c in comp)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        out.append({"x0": x0, "x1": x1 + 1, "y0": y0, "y1": y1 + 1,
                    "w": bw, "h": bh, "cells": len(comp),
                    "fill": len(comp) / float(bw * bh)})
    return out


def detect_legend(rgb, box, colors, max_width_frac=0.16):
    """Find a legend key from the shape of the coloured marks inside the frame.

    Two legend styles have to be handled, and neither is found by assuming a
    corner:

      * solid swatch (bar / patch legends)  - compact box, fill ratio near 1
      * line handle (matplotlib ``plot``)   - a short horizontal dash, tall
                                              only by the stroke width, fill
                                              ratio low but *width bounded*

    What separates both from real data is width: a handle is a small fraction of
    the plot width, whereas a curve spans it. Requiring at least two distinct
    series colours in one vertically stacked group then rules out an isolated
    marker sitting on a kink of a single curve.
    """
    l, t, r, b = box
    plot_w = max(1, r - l)
    per_color = {}
    for name, spec in colors.items():
        tol = int(spec.get("tol", 70)) if isinstance(spec, dict) else 70
        c = spec["rgb"] if isinstance(spec, dict) else spec
        m = color_mask(rgb, c, tol)
        m[:t, :] = False
        m[b:, :] = False
        m[:, :l] = False
        m[:, r:] = False
        per_color[name] = [c for c in components(m)
                           if c["w"] <= max_width_frac * plot_w and c["w"] >= 4 and c["h"] >= 1]
    if not per_color:
        return None

    flat = [(n, c) for n, cs in per_color.items() for c in cs]
    if len({n for n, _ in flat}) < 2:
        return None

    best = None
    for i, (n, c) in enumerate(flat):
        group = [(n, c)]
        ys = [c["y0"]]
        for j, (n2, c2) in enumerate(flat):
            if j == i or n2 == n:
                continue
            # Legend entries sit in one column with identical handle geometry
            # (matplotlib draws every handle the same length). Matching on
            # handle shape as well as alignment keeps a stray data marker of
            # a second colour from being mistaken for an entry.
            same_column = abs(c2["x0"] - c["x0"]) <= 3
            same_handle = abs(c2["w"] - c["w"]) <= 2 and abs(c2["h"] - c["h"]) <= 2
            if same_column and same_handle and c2["y0"] != c["y0"]:
                group.append((n2, c2))
                ys.append(c2["y0"])
        names = {g[0] for g in group}
        if len(names) < 2:
            continue
        # entries are stacked closely; a taller spread is a coincidence
        if max(ys) - min(ys) > 0.25 * (b - t):
            continue
        score = (len(names), len(group))
        if best is None or score > best[0]:
            best = (score, group)
    if best is None:
        return None
    group = best[1]
    gx0 = min(g[1]["x0"] for g in group)
    gx1 = max(g[1]["x1"] for g in group)
    gy0 = min(g[1]["y0"] for g in group)
    gy1 = max(g[1]["y1"] for g in group)
    # cover the text labels to the right of the handles
    bx1 = min(r, gx1 + int(0.22 * plot_w))
    by1 = min(b, gy1 + int(0.02 * (b - t)) + 3)
    return {"box": [int(max(l, gx0 - 3)), int(max(t, gy0 - 3)), int(bx1), int(by1)],
            "entries": [{"name": n, "handle_px": [c["x0"], c["y0"], c["x1"], c["y1"]]}
                        for n, c in group],
            "method": ("components narrower than %.0f%% of the plot width, two or more "
                       "series colours stacked in one column" % (100 * max_width_frac))}


def simplify(pts, tol):
    """Douglas-Peucker on (x, y) pixel pairs; keeps shape, drops redundancy."""
    if len(pts) <= 2 or tol <= 0:
        return list(range(len(pts)))
    keep = [0, len(pts) - 1]
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        x0, y0 = pts[i]
        x1, y1 = pts[j]
        dx, dy = x1 - x0, y1 - y0
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-9
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            d = abs(dy * (px - x0) - dx * (py - y0)) / norm
            if d > best:
                best, bi = d, k
        if best > tol:
            keep.append(bi)
            stack.append((i, bi))
            stack.append((bi, j))
    return sorted(set(keep))



# ---------------------------------------------------------------- probe

def probe(path):
    """Describe an image without assuming it is a chart."""
    rgb, meta = load(path)
    lum = luma(rgb)
    h, w = lum.shape
    dark = lum < DARK
    dark_frac = float(dark.mean())

    # background uniformity: share of pixels close to the modal luminance
    hist, edges = np.histogram(lum, bins=32, range=(0, 255))
    modal = int(hist.argmax())
    modal_centre = (edges[modal] + edges[modal + 1]) / 2
    near_modal = float((np.abs(lum - modal_centre) <= 8).mean())

    # colour richness: photographs have broad hue spread, charts few flat colours
    r, g, bb = rgb[:, :, 0].astype(int), rgb[:, :, 1].astype(int), rgb[:, :, 2].astype(int)
    sat = (np.maximum(np.maximum(r, g), bb) - np.minimum(np.minimum(r, g), bb))
    colourful = float((sat > 40).mean())
    q = (rgb // 24).astype(np.int32)
    keys = q[:, :, 0] * 10000 + q[:, :, 1] * 100 + q[:, :, 2]
    uniq = int(np.unique(keys).size)
    flat_fraction = float((np.bincount(keys.ravel(), minlength=1).max() / keys.size))

    horiz, vert = detect_lines(dark)
    long_h = [ln for ln in horiz if ln["len"] >= 0.45 * w]
    long_v = [ln for ln in vert if ln["len"] >= 0.45 * h]

    frame = None
    if long_v and long_h:
        left = min(long_v, key=lambda d: d["x"])
        right = max(long_v, key=lambda d: d["x"])
        top = min(long_h, key=lambda d: d["y"])
        bottom = max(long_h, key=lambda d: d["y"])
        if left["x"] < right["x"] and top["y"] < bottom["y"]:
            frame = [int(left["x"]), int(top["y"]), int(right["x"]), int(bottom["y"])]

    has_frame = frame is not None
    # a chart frame is a tall+wide box enclosing a large paper area
    area_frac = ((frame[2] - frame[0]) * (frame[3] - frame[1]) / (w * h)) if frame else 0.0

    if has_frame and near_modal >= 0.35 and area_frac >= 0.10:
        kind = "chart_with_axes"
        why = (f"found a rectangular axis frame {frame} covering {area_frac:.0%} of the image, "
               f"on a background that is {near_modal:.0%} one flat tone")
    elif has_frame:
        kind = "line_drawing"
        why = f"straight frame {frame} present but background is not flat enough for a plot area"
    elif near_modal >= 0.45 and uniq <= 400:
        kind = "diagram_or_schematic"
        why = (f"no axis frame; background is {near_modal:.0%} flat and only {uniq} distinct "
               f"24-level colour bins - drawn artwork rather than a photograph")
    else:
        kind = "photograph"
        why = (f"no axis frame; luminance is spread rather than modal ({near_modal:.0%} near the "
               f"modal tone) and there are {uniq} distinct colour bins")

    ticks = {"x": [], "y": []}
    if frame:
        l, t, r, b = frame
        ticks["x"] = find_ticks(dark, "x", b, (l, r), "below")
        ticks["y"] = find_ticks(dark, "y", l, (t, b), "left")
        if not ticks["x"]:
            ticks["x"] = find_ticks(dark, "x", b, (l, r), "above")
        if not ticks["y"]:
            ticks["y"] = find_ticks(dark, "y", l, (t, b), "right")

    return {
        "image": meta,
        "probe_version": VERSION,
        "verdict": kind,
        "verdict_reason": why,
        "geometry": {
            "frame_px": frame,
            "frame_area_fraction": round(area_frac, 4),
            "long_horizontal_lines": long_h[:6],
            "long_vertical_lines": long_v[:6],
        },
        "statistics": {
            "dark_pixel_fraction": round(dark_frac, 4),
            "background_near_modal_fraction": round(near_modal, 4),
            "flat_colour_bin_fraction": round(flat_fraction, 4),
            "distinct_colour_bins": uniq,
            "saturated_pixel_fraction": round(colourful, 4),
        },
        "tick_candidates": ticks,
        "quantitative_objects": ("axes_detected" if kind == "chart_with_axes"
                                 else "none_detected"),
        "next_step": ("calibrate with `run`/`calibrate`; supply tick values you can read"
                      if kind == "chart_with_axes" else
                      "no quantitative axis object in this image; do not invent axes or data points "
                      "- report quantitative fields as not_applicable for this object"),
    }


# ---------------------------------------------------------------- calibration

def make_calibration(spec, meta):
    """Build both axis calibrations from the reader-supplied tick table."""
    out = {}
    for axis in ("x", "y"):
        a = spec.get(axis) or {}
        px = a.get("ticks_px") or []
        vals = a.get("values") or []
        if not px or not vals:
            out[axis] = {"status": "not_reported",
                         "reason": "no tick pixels/values supplied for this axis"}
            continue
        fit = fit_scale(px, vals)
        chosen = a.get("scale") if a.get("scale") in ("linear", "log10") else fit["verdict"]
        ref = fit["log10"] if chosen == "log10" else fit["linear"]
        if ref is None:
            chosen, ref = "linear", fit["linear"]
        out[axis] = {
            "status": "calibrated",
            "scale": chosen,
            "scale_reason": fit["reason"],
            "a": ref["a"], "b": ref["b"],
            "r2": ref["r2"],
            "rmse_px": ref["rmse_px"],
            "max_abs_residual_px": ref["max_abs_residual_px"],
            "alternate_fit": (fit["linear"] if chosen == "log10" else fit["log10"]),
            "ticks_used": fit["ticks_used"],
            "label": a.get("label"),
            "unit": a.get("unit"),
            "unit_status": "reported" if a.get("unit") else "not_reported",
        }
    return out


def _axis_to_json(cal):
    return cal


def calibrate_spec(spec):
    path = spec.get("image")
    if not path:
        raise ValueError("spec.image is required")
    probe_res = probe(path)
    meta = probe_res["image"]
    box = (spec.get("frame_px") or probe_res["geometry"]["frame_px"])
    cals = make_calibration(spec, meta)
    return probe_res, box, cals


# ---------------------------------------------------------------- digitising

def digitize(spec, box, cals, workdir=None):
    rgb, meta = load(spec["image"])
    fname = meta["file"]
    if not box:
        return {"status": "unavailable", "reason": "no plot frame; nothing to digitise"}
    l, t, r, b = box

    series_specs = spec.get("series") or []
    color_table = {}
    for s in series_specs:
        if s.get("color"):
            color_table[s.get("name")] = {"rgb": s["color"], "tol": int(s.get("tol") or 70)}
    legend = detect_legend(rgb, box, color_table) if spec.get("detect_legend", True) else None
    # Only the handle pixels and the label area beside them are removed. The
    # whole bounding box is NOT blanked: a curve that passes behind the legend
    # is still real data, and deleting a rectangle of it would silently punch a
    # hole in the digitised series.
    legend_hits = []
    if legend:
        lx0, ly0, lx1, ly1 = legend["box"]
        for e in legend["entries"]:
            hx0, hy0, hx1, hy1 = e["handle_px"]
            legend_hits.append((hx0, hy0, hx1, hy1))

    results = []
    for s in series_specs:
        color = s.get("color")
        if not color:
            results.append({"name": s.get("name"), "status": "not_reported",
                            "reason": "no colour given, cannot isolate the series"})
            continue
        tol = int(s.get("tol") or 70)
        mask = color_mask(rgb, color, tol)
        for hx0, hy0, hx1, hy1 in legend_hits:
            mask[max(0, hy0 - 1):hy1 + 1, max(0, hx0 - 1):hx1 + 1] = False
        mask[:t, :] = False
        mask[b:, :] = False
        mask[:, :l] = False
        mask[:, r:] = False

        segs = digitize_series(rgb, mask, box)
        segs = [g for g in segs if len(g) >= int(s.get("min_points") or 2)]
        thickness = stroke_thickness(segs[0]) if segs else 0.0
        calx, caly = cals["x"], cals["y"]
        ok = calx.get("status") == "calibrated" and caly.get("status") == "calibrated"
        tol_px = float(s.get("simplify_px") or 1.0)
        out_segs = []
        for gi, seg in enumerate(segs):
            flat = [(x, ymid) for x, ymid, _, _, _ in seg]
            keep = simplify(flat, tol_px)
            pts = []
            for k in keep:
                x, ymid, ymin, ymax, n = seg[k]
                rec = {"locator": f"img:{fname}@{x},{int(round(ymid))}",
                       "px": x, "py": round(ymid, 2),
                       "ink_extent_px": ymax - ymin}
                if ok:
                    rec["x"] = _round(apply_scale(calx, [x])[0])
                    rec["y"] = _round(apply_scale(caly, [ymid])[0])
                    half = (ymax - ymin) / 2.0
                    rec["y_half_extent"] = _round(abs(
                        apply_scale(caly, [ymid + half])[0] - apply_scale(caly, [ymid])[0]))
                pts.append(rec)
            bars = detect_error_bars(seg, thickness)
            if ok:
                for bar in bars:
                    bar["y_half_span_data"] = _round(abs(
                        apply_scale(caly, [bar["top_px"]])[0]
                        - apply_scale(caly, [bar["bottom_px"]])[0]) / 2.0)
            out_segs.append({"segment": gi, "n_points": len(pts),
                             "n_columns_raw": len(seg),
                             "x_span_px": [seg[0][0], seg[-1][0]],
                             "points": pts, "error_bar_candidates": bars})
        results.append({
            "name": s.get("name"),
            "status": "digitized" if ok else "points_without_data_coordinates",
            "color_rgb": color, "tolerance": tol,
            "stroke_thickness_px": round(thickness, 2),
            "segments": out_segs,
            "n_points": sum(g["n_points"] for g in out_segs),
            "simplify_tolerance_px": tol_px,
            "uncertainty": ("read from the raster at 1 px resolution; y is a column centroid of the "
                            "stroke so half the stroke thickness bounds the reading"),
        })
    return {"status": "ok", "frame_px": list(box), "legend": legend, "series": results}


def _round(v, nd=4):
    return float(f"{float(v):.{nd}g}")


# ---------------------------------------------------------------- run

def run(spec):
    probe_res, box, cals = calibrate_spec(spec)
    dig = digitize(spec, box, cals)
    problems = []
    for axis in ("x", "y"):
        if cals[axis].get("status") == "calibrated" and cals[axis]["max_abs_residual_px"] > 3:
            problems.append(f"{axis} axis fit residual {cals[axis]['max_abs_residual_px']:.1f}px is "
                            f"large; the supplied tick values may be misread")
    if probe_res["verdict"] != "chart_with_axes":
        problems.append(f"image classified as {probe_res['verdict']}, not a chart with axes")
    return {
        "chart_parse_version": VERSION,
        "image": probe_res["image"],
        "probe": {k: probe_res[k] for k in ("verdict", "verdict_reason", "geometry",
                                            "statistics", "tick_candidates",
                                            "quantitative_objects")},
        "frame_px": box,
        "axes": cals,
        "series": dig.get("series") or [],
        "problems": problems,
        "method": {
            "calibration": "least squares of pixel_position = a + b*f(value), f = identity or log10",
            "scale_choice": "linear unless log10 residuals are <=1.5px AND 3x better than linear",
            "digitisation": "per-column centroid of a colour mask inside the plot frame",
            "tick_values": "supplied by the reader from the visible labels; never OCR-guessed",
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse a raster scientific figure into calibrated data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("image"); p.add_argument("-o", "--out")
    c = sub.add_parser("calibrate"); c.add_argument("spec"); c.add_argument("-o", "--out")
    d = sub.add_parser("digitize"); d.add_argument("image"); d.add_argument("--calib", required=True)
    d.add_argument("--spec"); d.add_argument("-o", "--out")
    r = sub.add_parser("run"); r.add_argument("spec"); r.add_argument("-o", "--out")
    sub.add_parser("version")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "version":
            print(json.dumps({"chart_parse_version": VERSION,
                              "numpy": bool(np is not None), "pillow": bool(Image is not None)}))
            return 0
        if a.cmd == "probe":
            res = probe(a.image)
        elif a.cmd == "calibrate":
            spec = json.load(open(a.spec, encoding="utf-8"))
            probe_res, box, cals = calibrate_spec(spec)
            res = {"image": probe_res["image"], "frame_px": box, "axes": cals,
                   "probe_verdict": probe_res["verdict"]}
        elif a.cmd == "digitize":
            cal = json.load(open(a.calib, encoding="utf-8"))
            spec = json.load(open(a.spec, encoding="utf-8")) if a.spec else {"series": cal.get("series")}
            spec.setdefault("image", a.image)
            res = digitize(spec, cal.get("frame_px"), cal.get("axes"))
        else:
            res = run(json.load(open(a.spec, encoding="utf-8")))
    except FileNotFoundError as exc:
        print(json.dumps({"error": "input_error", "missing_file": str(exc),
                          "hint": "the case references this path; list the actual material directory "
                                  "and report the file as absent rather than describing its contents"},
                         ensure_ascii=False, indent=1))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)[:400]},
                         ensure_ascii=False, indent=1))
        return 3

    text = json.dumps(res, ensure_ascii=False, indent=1)
    if getattr(a, "out", None):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(json.dumps({"out": a.out, "bytes": len(text)}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
