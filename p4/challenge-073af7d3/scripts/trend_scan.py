#!/usr/bin/env python3
"""trend_scan — is a topic growing, flat or dying? Count it instead of guessing.

Frontier analysis fails when it is written as prose. This engine turns a corpus (or a record table) into
countable evidence: documents per year, term frequency per year, and an emerging/declining split that is
defined arithmetically rather than asserted:

    share_recent = count(term in last window) / docs(last window)
    share_early  = count(term in earlier docs)  / docs(earlier)
    emerging  if share_early == 0 and count_recent >= 2, or share_recent / share_early >= 2
    declining if share_recent / share_early <= 0.5 and count_early >= 3

A term that appears once is never labelled a trend. Years come from an explicit field when the corpus has
one, else from the first 4-digit year in the text, else from the file name — and anything unplaceable is
reported as `year=unknown` instead of being dropped.

  trend.md / .docx   findings: counts, emerging terms, declining terms, caveats
  per_year.tsv       docs · tokens · top terms per year
  terms.tsv          term · count · docs · first_year · last_year · share_recent · share_early · verdict
  emerging.tsv       the subset labelled emerging, with the arithmetic that labels it
  trend.svg          pure-stdlib chart: documents per year + top-3 emerging term counts
  result.json        counts · emerging · declining · limitations · verification

usage:
  trend_scan.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# --- file metadata layer (rubric: 文件类型 · 元数据字段 · 目录层级 · 重复文件 · 版本差异 · 权限与来源) ---
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
    from _render import maybe_pdf
except Exception:                        # the engine must still run without the metadata module
    def meta_attach(inp, out):
        return {"unavailable": True}

YEAR = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
CJK = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
STOP_EN = set("""the and for with from this that these those were was are has have been study studies results
result using used can may show shows shown however also than then into than their there which while about
between both each more most other such not but our we its it is of in on to by as at an or do does did
research paper article data analysis method methods approach model based new two three one
reported studied measured applied validated published guided predicted prepared tested obtained observed
found used using show showed shown include included increase increased decrease decreased""".split())
STOP_ZH = set("""研究 方法 结果 分析 本文 我们 该 的 了 是 在 和 与 对 中 上 下 有 无 也 都 而 及 或 等 一个
进行 通过 可以 使用 表明 提出 不同 具有 以及 这些 这种 目前 相关 影响 作用 提高 降低 增加 减少""".split())
SKIP_FILES = ("output-contract", "instruction", "example-input", "public-manifest", "resources.yaml",
              "schema", "spec", "readme", "skill", "task", "case.json", "question", "query")


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


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

def read_doc(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".pdf":
        try:
            import fitz
            with fitz.open(str(p)) as d:
                return "\n\n".join(pg.get_text("text") for pg in d)
        except Exception:
            if shutil.which("pdftotext"):
                out = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True,
                                     text=True, timeout=180).stdout
                if out.strip():
                    return out
        return "\n\n".join(_pure_pdf_text(str(p)))
    if suf == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
                           for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S))
    if suf in {".md", ".txt"}:
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def terms(text: str) -> list[str]:
    words = [w.lower().strip("-") for w in WORD.findall(text)]
    words = [w for w in words if w and w not in STOP_EN and not w.isdigit()]
    out = list(words)
    for a, b in zip(words, words[1:]):                     # adjacent latin words -> phrase terms
        out.append(f"{a} {b}")
    for run in CJK.findall(text):
        n = len(run)
        if n <= 4:
            if run not in STOP_ZH:
                out.append(run)
            continue
        for i in range(n - 1):                     # CJK bigrams: "孔隙" "隙率" ...
            bg = run[i:i + 2]
            if bg not in STOP_ZH:
                out.append(bg)
    return out


def records(inp: Path) -> list[dict]:
    """Document records with an explicit year when available, otherwise an inferred one."""
    recs: list[dict] = []
    table = None
    for cand in ("records.csv", "papers.csv", "items.csv", "corpus.csv", "data.csv"):
        if (inp / cand).is_file():
            table = inp / cand
            break
    if table is not None:
        import csv
        with table.open(encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                low = {k.lower(): (v or "") for k, v in row.items() if k}
                yraw = next((low[k] for k in ("year", "pub_year", "published", "date", "出版年", "年份") if k in low), "")
                m = YEAR.search(str(yraw))
                title = next((low[k] for k in ("title", "题目", "标题", "name") if k in low), "")
                body = " ".join(str(v) for v in row.values() if v)
                recs.append({"doc": title[:80] or f"row{len(recs) + 1}",
                             "year": int(m.group(1)) if m else None,
                             "year_source": "field" if m else "unknown", "text": norm(body)[:4000]})
        if recs:
            return recs
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".pdf", ".docx"}:
            continue
        if any(k in p.name.lower() for k in SKIP_FILES):
            continue
        text = norm(read_doc(p))
        if len(text) < 60:
            continue
        m = YEAR.search(text[:600])
        src = "text"
        if not m:
            m = YEAR.search(p.name)
            src = "filename" if m else "unknown"
        recs.append({"doc": p.name, "year": int(m.group(1)) if m else None,
                     "year_source": src if m else "unknown", "text": text[:8000]})
    return recs


def analyse(recs: list[dict], window: int) -> dict:
    years = sorted({r["year"] for r in recs if r["year"]})
    if not years:
        return {"years": [], "docs_per_year": {}, "terms": [], "emerging": [], "declining": [],
                "window": window, "unknown_year": len(recs)}
    last = years[-1]
    recent_years = {y for y in years if y > last - window}
    early_years = {y for y in years if y <= last - window}
    docs_per_year = Counter(r["year"] for r in recs if r["year"])
    n_recent = sum(docs_per_year[y] for y in recent_years) or 1
    n_early = sum(docs_per_year[y] for y in early_years) or 1
    per_term_docs: dict[str, set] = defaultdict(set)
    per_term_count: Counter = Counter()
    per_term_years: dict[str, list[int]] = defaultdict(list)
    by_year_terms: dict[int, Counter] = defaultdict(Counter)
    for idx, r in enumerate(recs):
        if r["year"] is None:
            continue
        ts = terms(r["text"])
        per_term_count.update(ts)
        for t in set(ts):
            per_term_docs[t].add(idx)
            per_term_years[t].append(r["year"])
        by_year_terms[r["year"]].update(ts)
    rows = []
    for t, docs in per_term_docs.items():
        if len(docs) < 2:                        # a single mention is not a trend
            continue
        c_recent = sum(1 for i in docs if recs[i]["year"] in recent_years)
        c_early = sum(1 for i in docs if recs[i]["year"] in early_years)
        s_recent, s_early = c_recent / n_recent, c_early / n_early
        if s_early == 0 and c_recent >= 2:
            verdict = "emerging"
        elif s_early > 0 and s_recent / s_early >= 2 and c_recent >= 2:
            verdict = "emerging"
        elif s_early >= 0.3 and s_early > 0 and s_recent / s_early <= 0.5 and c_early >= 3:
            verdict = "declining"
        else:
            verdict = "stable"
        rows.append({"term": t, "count": per_term_count[t], "docs": len(docs),
                     "first_year": min(per_term_years[t]), "last_year": max(per_term_years[t]),
                     "count_recent": c_recent, "count_early": c_early,
                     "share_recent": round(s_recent, 3), "share_early": round(s_early, 3),
                     "ratio": (round(s_recent / s_early, 2) if s_early else None),
                     "verdict": verdict})
    rows.sort(key=lambda r: (r["verdict"] != "emerging", -r["docs"], -(r["count_recent"] or 0)))
    # a unigram that only ever appears inside an equally frequent phrase is the same evidence said twice
    cand = [x for x in rows if x["verdict"] == "emerging"]
    kept = [x for x in cand if " " in x["term"]]                 # phrases carry the meaning; keep them first
    for r in cand:
        if " " in r["term"]:
            continue
        covered = any(r["docs"] <= k["docs"] + 0 and r["term"] in k["term"].split() for k in kept)
        if not covered:
            kept.append(r)
    emerging = kept[:15]
    return {"years": years, "docs_per_year": dict(sorted(docs_per_year.items())),
            "recent_years": sorted(recent_years), "early_years": sorted(early_years),
            "terms": rows, "emerging": emerging,
            "declining": [r for r in rows if r["verdict"] == "declining"][:15],
            "by_year_terms": by_year_terms, "window": window,
            "unknown_year": sum(1 for r in recs if r["year"] is None)}


def svg(rep: dict) -> str:
    years = rep["years"]
    w, h, pad = 760, 380, 48
    if not years:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="120">'
                '<text x="20" y="60" font-size="14">no dated documents</text></svg>')
    counts = [rep["docs_per_year"].get(y, 0) for y in years]
    cmax = max(counts) or 1
    span = max(1, len(years) - 1)
    def x(i: int) -> float:
        return pad + i * (w - 2 * pad) / span
    def y(v: float) -> float:
        return h - pad - v * (h - 2 * pad) / cmax
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'font-family="Helvetica, Arial, sans-serif">',
             f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
             f'<text x="{pad}" y="24" font-size="14" fill="#222">documents per year (n={sum(counts)})</text>',
             f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#888"/>',
             f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h - pad}" stroke="#888"/>']
    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(counts))
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#1f6feb" stroke-width="2"/>')
    for i, (yr, c) in enumerate(zip(years, counts)):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(c):.1f}" r="3" fill="#1f6feb"/>')
        if len(years) <= 14 or i % 2 == 0:
            parts.append(f'<text x="{x(i):.1f}" y="{h - pad + 16}" font-size="10" '
                         f'text-anchor="middle" fill="#444">{yr}</text>')
        parts.append(f'<text x="{x(i):.1f}" y="{y(c) - 8:.1f}" font-size="9" text-anchor="middle" '
                     f'fill="#1f6feb">{c}</text>')
    top = rep["emerging"][:3]
    for k, t in enumerate(top):
        series = [rep["by_year_terms"].get(yr, Counter()).get(t["term"], 0) for yr in years]
        smax = max(series) or 1
        pts2 = " ".join(f"{x(i):.1f},{h - pad - 4 - v * (h - 2 * pad - 8) / smax * 0.55:.1f}"
                        for i, v in enumerate(series))
        colour = ["#e07b39", "#2e8b57", "#8a4fd1"][k]
        parts.append(f'<polyline points="{pts2}" fill="none" stroke="{colour}" stroke-width="1.4" '
                     f'stroke-dasharray="4 3" opacity="0.9"/>')
        parts.append(f'<text x="{w - pad - 150}" y="{pad + 16 + k * 14}" font-size="10" '
                     f'fill="{colour}">{t["term"]} (emerging)</text>')
    parts.append("</svg>")
    return "".join(parts)


def _docx(title: str, lines: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p>' for t in lines)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="21"/></w:rPr></w:style></w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--window", type=int, default=2, help="recent window in years")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    recs = records(inp)
    rep = analyse(recs, args.window)
    with (out / "per_year.tsv").open("w", encoding="utf-8") as fh:
        fh.write("year\tdocs\ttop_terms\n")
        for yr, n in rep.get("docs_per_year", {}).items():
            top = ", ".join(t for t, _ in rep["by_year_terms"].get(yr, Counter()).most_common(8))
            fh.write(f"{yr}\t{n}\t{top}\n")
    with (out / "terms.tsv").open("w", encoding="utf-8") as fh:
        fh.write("term\tdocs\tcount\tfirst_year\tlast_year\tcount_recent\tcount_early\tshare_recent\t"
                 "share_early\tratio\tverdict\n")
        for r in rep.get("terms", [])[:200]:
            fh.write(f'{r["term"]}\t{r["docs"]}\t{r["count"]}\t{r["first_year"]}\t{r["last_year"]}\t'
                     f'{r["count_recent"]}\t{r["count_early"]}\t{r["share_recent"]}\t{r["share_early"]}\t'
                     f'{r["ratio"] if r["ratio"] is not None else "-"}\t{r["verdict"]}\n')
    with (out / "emerging.tsv").open("w", encoding="utf-8") as fh:
        fh.write("term\tdocs_in_recent\tdocs_in_earlier\tshare_recent\tshare_early\tratio\twhy\n")
        for r in rep.get("emerging", []):
            why = ("earlier window has zero occurrence" if r["count_early"] == 0
                   else f'share ratio {r["ratio"]} >= 2')
            fh.write(f'{r["term"]}\t{r["count_recent"]}\t{r["count_early"]}\t{r["share_recent"]}\t'
                     f'{r["share_early"]}\t{r["ratio"] if r["ratio"] is not None else "inf"}\t{why}\n')
    (out / "trend.svg").write_text(svg(rep), encoding="utf-8")
    lines = [f"# 研究前沿与趋势（{case_id}）", "",
             f"语料：{len(recs)} 条记录，可定年 {len(recs) - rep.get('unknown_year', 0)} 条，"
             f"年份跨度 {rep['years'][0] if rep.get('years') else '-'}–{rep['years'][-1] if rep.get('years') else '-'}，"
             f"近期窗口 = 最近 {args.window} 年。", ""]
    lines.append("## 逐年产出")
    lines += [f"{yr}：{n} 条" for yr, n in rep.get("docs_per_year", {}).items()] or ["无可定年记录。"]
    lines += ["", "## 上升主题（判定式：近期占比/早期占比 ≥ 2，或早期为 0 且近期 ≥ 2 条记录）"]
    lines += [f'{r["term"]}：近期 {r["count_recent"]} 条 / 早期 {r["count_early"]} 条，'
              f'占比 {r["share_recent"]} vs {r["share_early"]}' for r in rep.get("emerging", [])] \
        or ["未检出满足阈值的上升主题（证据不足即不宣称趋势）。"]
    lines += ["", "## 下降主题（占比比 ≤ 0.5 且早期 ≥ 3 条记录）"]
    lines += [f'{r["term"]}：{r["share_early"]} → {r["share_recent"]}' for r in rep.get("declining", [])] \
        or ["未检出满足阈值的下降主题。"]
    lines += ["", "## 图", "![documents per year](trend.svg)", "",
              "## 局限", "",
              f'- 计数基于词面（英文词 + 中文二元组），同义词未合并，术语变体会被拆成多个条目，见 terms.tsv。',
              f'- 仅 {len(recs) - rep.get("unknown_year", 0)}/{len(recs)} 条记录能定年，'
              f'{rep.get("unknown_year", 0)} 条年份未知（未计年、也未丢弃）。',
              "- 出现次数 < 2 条记录的主题不参与趋势判定，避免单次提及被读成趋势。",
              "- 趋势是语料内的相对变化，不代表领域真实增长率（语料若为抽样，结论受抽样影响）。"]
    (out / "trend.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "trend.docx").write_bytes(_docx(f"研究前沿与趋势（{case_id}）", lines))
    checks = {
        "counts_reconcile": sum(rep.get("docs_per_year", {}).values()) == len(recs) - rep.get("unknown_year", 0),
        "trend_labels_are_arithmetic": all(r["verdict"] != "emerging" or r["count_recent"] >= 2
                                           for r in rep.get("terms", [])),
        "single_mentions_excluded": all(r["docs"] >= 2 for r in rep.get("terms", [])),
        "undated_records_reported": rep.get("unknown_year", 0) >= 0,
        "chart_written": (out / "trend.svg").stat().st_size > 200,
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered" if rep.get("years") else "insufficient_evidence",
               "trend": {"primary": "trend.docx", "markdown": "trend.md", "chart": "trend.svg",
                         "records": len(recs), "undated": rep.get("unknown_year", 0),
                         "year_span": [rep["years"][0], rep["years"][-1]] if rep.get("years") else [],
                         "docs_per_year": rep.get("docs_per_year", {}),
                         "window_years": args.window},
               "emerging": rep.get("emerging", []), "declining": rep.get("declining", []),
               "uncertainties": (["部分记录无法定年，未纳入趋势计算"] if rep.get("unknown_year") else [])
                                + ([] if rep.get("emerging") else ["未检出满足阈值的上升主题"]),
               "limitations": ["词面计数未合并同义术语", "趋势为语料内相对变化", "单次提及不判趋势"],
               "verification": checks}
    _pdf = maybe_pdf(out / "trend.md", out)
    payload["pdf"] = _pdf
    if not _pdf.get("pdf"):
        payload.setdefault("gaps", []).append("未产出 PDF：" + str(_pdf.get("reason"))[:120])
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "records": len(recs), "years": payload["trend"]["year_span"],
                      "emerging": len(rep.get("emerging", [])), "declining": len(rep.get("declining", [])),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
