#!/usr/bin/env python3
"""Segment -> (agent translates) -> assemble: the deterministic half of a bilingual job.

The model does the translating; this script does everything that must NOT depend on
the model's memory: segment ids and locators, verbatim protection of formulas /
references / code, per-segment number / citation / negation / hedge / glossary checks,
the editable bilingual .docx, its real rendered PDF+PNG preview, and a result.json
carrying every output_contract field.

  python3 bilingual.py segment  --source FILE [--lines A-B] [--work DIR]
        -> DIR/segments.json, DIR/translations.todo.json, DIR/term_candidates.json
  # agent writes DIR/translations.json   {"S0001": "译文", ...}  (or {"S0001": {"tgt":..,"note":..}})
  # optional    DIR/glossary.json         {"source term": "目标术语", ...}
  python3 bilingual.py assemble --case /input/case.json --work DIR --outdir /app/output
        [--font "Liberation Serif"] [--cjk-font "Noto Serif CJK SC"] [--no-render]

Exit codes: 0 = written (check status in result.json), 2 = unusable input (a structured
input_error result.json is still written when --outdir is known). Never needs network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import text_checks as tc  # noqa: E402  shared tokenisers, one definition of "a number"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
NEG = {"en": re.compile(r"\b(not|no|never|neither|nor|none|without|cannot|absence|lack(?:ed|s|ing)?|fail(?:ed|s)?\s+to|n't)\b", re.I),
       "zh": re.compile(r"[不未没无非否勿]|缺乏|尚未|并非")}
HEDGE = {"en": re.compile(r"\b(may|might|could|possibly|likely|unlikely|suggests?|suggested|appears?|seems?|approximately|about|around|roughly|presumably|potential(?:ly)?|indicates?)\b", re.I),
         "zh": re.compile(r"可能|或许|也许|提示|似乎|大约|约(?=\s*[0-9０-９])|左右|潜在|倾向|推测|疑似|表明|显示")}
LATIN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9+\-]*|[A-Za-z]+\d+[A-Za-z0-9+\-]*)(?![A-Za-z0-9])")
ABBR = r"(?:e\.g|i\.e|et al|Fig|Figs|Eq|Eqs|Ref|Refs|vs|approx|ca|cf|No|Vol|pp|Dr|Mr|Ms|Sect|Tab)\."
EN_SPLIT_RE = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z(\[\"'])")
ZH_SPLIT_RE = re.compile(r"(?<=[。！？])")
FORMULA_RE = re.compile(r"^\s*(\$\$.*|\\\[.*|\\begin\{(equation|align)|.*\\(frac|sum|int|alpha|beta|mathrm)\b)")
REF_HEAD_RE = re.compile(r"^\s*#*\s*(references|bibliography|literature cited|参考文献)\s*$", re.I)
CAPTION_RE = re.compile(r"^\s*(fig(?:ure)?\.?|table|tab\.|图|表)\s*[0-9IVX]+", re.I)


def lang_of(text: str) -> str:
    letters = sum(c.isalpha() for c in text) or 1
    return "zh" if len(CJK_RE.findall(text)) / letters > 0.3 else "en"


# --------------------------------------------------------------------------- reading
def read_text_file(path: str) -> tuple[str, list]:
    warns = []
    raw = open(path, "rb").read()
    for enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8":
                warns.append({"code": "encoding_fallback", "detail": f"decoded as {enc}, not utf-8"})
            return text, warns
        except UnicodeDecodeError:
            continue
    warns.append({"code": "encoding_error", "detail": "undecodable bytes replaced (U+FFFD); affected text needs human check"})
    return raw.decode("utf-8", errors="replace"), warns


def read_any(path: str, lines: tuple[int, int] | None = None) -> tuple[list, dict]:
    """Return ([{locator, kind, text}], meta). Paragraph units, before sentence split."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    meta = {"source": path, "engine": None, "warnings": []}
    units = []
    if "#" in path and path.split("#", 1)[0].lower().endswith(".json"):
        # inline source text: case.json#source_text
        jpath, key = path.split("#", 1)
        val = json.loads(Path(jpath).read_text(encoding="utf-8")).get(key)
        if not isinstance(val, str):
            raise ValueError(f"{jpath} has no string field {key!r}")
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(val)
        try:
            sub, m2 = read_any(fh.name, lines)
        finally:
            os.unlink(fh.name)
        for u in sub:
            u["locator"] = f"{os.path.basename(jpath)}#{key}:" + u["locator"].split(":", 1)[1]
        m2["source"] = path
        return sub, m2
    if ext == ".pdf":
        import pdf_text  # type: ignore
        res = pdf_text.extract(path)
        meta["engine"] = "pdf_text/" + str(res.get("engine"))
        meta["warnings"] += [{"code": "pdf", "detail": w} for w in res.get("warnings", [])]
        for b in res["blocks"]:
            kind = {"reference": "reference", "caption": "caption", "heading": "heading",
                    "footnote": "footnote"}.get(b.get("type"), "body")
            units.append({"locator": f"{name}#{b['id']}", "kind": kind, "text": b["text"]})
        if not units:
            meta["warnings"].append({"code": "no_text_layer", "detail": "PDF yielded no text; OCR is not attempted"})
        return units, meta
    if ext == ".docx":
        meta["engine"] = "docx-xml"
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        for i, p in enumerate(re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S), 1):
            t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
            t = re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", re.sub(r"&amp;", "&", t)))
            if t.strip():
                kind = "heading" if "Heading" in p or "Title" in p else "body"
                units.append({"locator": f"{name}#p{i}", "kind": kind, "text": t.strip()})
        return units, meta
    text, warns = read_text_file(path)
    meta["engine"] = "text"
    meta["warnings"] += warns
    in_code, in_refs, buf, start = False, False, [], None
    all_lines = text.splitlines()
    lo, hi = lines if lines else (1, len(all_lines))

    def flush():
        nonlocal buf, start
        if buf:
            body = " ".join(x.strip() for x in buf) if not CJK_RE.search("".join(buf)) else "".join(x.strip() for x in buf)
            units.append({"locator": f"{name}:L{start}", "kind": "reference" if in_refs else "body", "text": body})
        buf, start = [], None

    for i, line in enumerate(all_lines, 1):
        if i < lo or i > hi:
            continue
        s = line.rstrip()
        if s.strip().startswith("```"):
            flush(); in_code = not in_code
            units.append({"locator": f"{name}:L{i}", "kind": "verbatim", "text": s}); continue
        if in_code:
            units.append({"locator": f"{name}:L{i}", "kind": "verbatim", "text": s}); continue
        if not s.strip():
            flush(); continue
        if REF_HEAD_RE.match(s):
            flush(); in_refs = True
            units.append({"locator": f"{name}:L{i}", "kind": "heading", "text": s.strip()}); continue
        if s.lstrip().startswith("#"):
            flush(); units.append({"locator": f"{name}:L{i}", "kind": "heading", "text": s.strip()}); continue
        if s.lstrip().startswith("|"):
            flush()
            kind = "table_rule" if re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", s) else "table_row"
            units.append({"locator": f"{name}:L{i}", "kind": kind, "text": s.strip()}); continue
        if FORMULA_RE.match(s):
            flush(); units.append({"locator": f"{name}:L{i}", "kind": "formula", "text": s.strip()}); continue
        if in_refs:
            flush(); units.append({"locator": f"{name}:L{i}", "kind": "reference", "text": s.strip()}); continue
        if CAPTION_RE.match(s):
            flush(); units.append({"locator": f"{name}:L{i}", "kind": "caption", "text": s.strip()}); continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", s):
            flush(); units.append({"locator": f"{name}:L{i}", "kind": "list_item", "text": s.strip()}); continue
        if start is None:
            start = i
        buf.append(s)
    flush()
    return units, meta


def split_sentences(text: str, lang: str) -> list[str]:
    if lang == "zh":
        parts = [p for p in ZH_SPLIT_RE.split(text) if p.strip()]
    else:
        protected = re.sub(ABBR, lambda m: m.group(0).replace(".", "\u2024"), text)
        parts = [p.replace("\u2024", ".") for p in EN_SPLIT_RE.split(protected) if p.strip()]
    return parts or [text]


VERBATIM_KINDS = {"formula", "verbatim", "table_rule", "reference"}


def cmd_segment(a) -> int:
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    rng = None
    if a.lines:
        lo, hi = a.lines.split("-"); rng = (int(lo), int(hi))
    if not os.path.isfile(a.source.split("#", 1)[0]):
        print(json.dumps({"ok": False, "error": f"source not found: {a.source}"}, ensure_ascii=False)); return 2
    try:
        units, meta = read_any(a.source, rng)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"unreadable source {a.source}: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False)); return 2
    if not units:
        print(json.dumps({"ok": False, "error": "source has no extractable text", "warnings": meta.get("warnings")},
                         ensure_ascii=False)); return 2
    src_lang = a.src_lang if a.src_lang != "auto" else lang_of(" ".join(u["text"] for u in units)[:20000])
    prev = None
    if a.append and (work / "segments.json").is_file():
        prev = json.loads((work / "segments.json").read_text(encoding="utf-8"))
    segs = list(prev["segments"]) if prev else []
    n, p0 = len(segs), (max((x["para"] for x in segs), default=0) if prev else 0)
    for pi, u in enumerate(units, p0 + 1):
        sents = [u["text"]] if u["kind"] in VERBATIM_KINDS | {"heading", "table_row", "caption"} else split_sentences(u["text"], src_lang)
        for si, s in enumerate(sents, 1):
            n += 1
            segs.append({"id": f"S{n:04d}", "para": pi, "sent": si, "locator": u["locator"] + (f":s{si}" if len(sents) > 1 else ""),
                         "kind": u["kind"], "src": s.strip(),
                         "keep_verbatim": u["kind"] in VERBATIM_KINDS and not a.translate_refs})
    tgt_lang = a.tgt_lang if a.tgt_lang != "auto" else ("en" if src_lang == "zh" else "zh")
    if prev:
        src_lang = prev["src_lang"] if a.src_lang == "auto" else src_lang
    seg_doc = {"source": (prev["source"] + " + " if prev else "") + os.path.abspath(a.source), "src_lang": src_lang, "tgt_lang": tgt_lang,
               "reader": meta, "line_range": a.lines, "segments": segs}
    (work / "segments.json").write_text(json.dumps(seg_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    todo = [{"id": s["id"], "kind": s["kind"], "src": s["src"],
             "tgt": s["src"] if s["keep_verbatim"] else ""} for s in segs]
    (work / "translations.todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    cands = Counter(m.group(1) for s in segs for m in LATIN_TOKEN_RE.finditer(s["src"]))
    (work / "term_candidates.json").write_text(json.dumps(
        [{"term": t, "count": c, "first": next(s["id"] for s in segs if t in s["src"])} for t, c in cands.most_common()],
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "segments": len(segs), "to_translate": sum(not s["keep_verbatim"] for s in segs),
                      "verbatim": sum(s["keep_verbatim"] for s in segs), "src_lang": src_lang, "tgt_lang": tgt_lang,
                      "engine": meta["engine"], "warnings": meta["warnings"], "work": str(work)}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- checks
def _markers(text):
    return sorted(m.group(0).replace(" ", "") for m in tc.MARKER_RE.finditer(text))


def check_segment(seg, tgt, src_lang, tgt_lang, glossary) -> list:
    flags = []
    src = seg["src"]
    if not tgt.strip():
        return [{"code": "untranslated", "detail": "no target text supplied"}]
    if seg["keep_verbatim"]:
        if tgt.strip() != src.strip():
            flags.append({"code": "verbatim_changed", "detail": "formula/reference/code must stay byte-identical"})
        return flags
    sn, tn = Counter(tc._tokens(src)), Counter(tc._tokens(tgt))
    missing = {k: v - tn.get(k, 0) for k, v in sn.items() if tn.get(k, 0) < v}
    added = {k: v - sn.get(k, 0) for k, v in tn.items() if sn.get(k, 0) < v}
    if missing:
        flags.append({"code": "number_missing", "detail": missing})
    if added:
        flags.append({"code": "number_added", "detail": added})
    sm, tm = _markers(src), _markers(tgt)
    if sm != tm:
        flags.append({"code": "citation_mismatch", "detail": {"src": sm, "tgt": tm}})
    if NEG[src_lang].search(src) and not NEG[tgt_lang].search(tgt):
        flags.append({"code": "negation_dropped", "detail": NEG[src_lang].findall(src)})
    if not NEG[src_lang].search(src) and NEG[tgt_lang].search(tgt) and tgt_lang == "en":
        flags.append({"code": "negation_added", "detail": NEG[tgt_lang].findall(tgt)})
    if HEDGE[src_lang].search(src) and not HEDGE[tgt_lang].search(tgt):
        flags.append({"code": "hedge_dropped", "detail": HEDGE[src_lang].findall(src)})
    if tgt_lang == "zh":
        for m in LATIN_TOKEN_RE.finditer(src):
            if m.group(1) not in tgt and m.group(1) not in glossary:
                flags.append({"code": "symbol_or_acronym_missing", "detail": m.group(1)})
    for s_term, t_term in glossary.items():
        if s_term.lower() in src.lower() and t_term not in tgt:
            flags.append({"code": "glossary_violation", "detail": {"term": s_term, "expected": t_term}})
    ratio = len(tgt) / max(1, len(src))
    lo, hi = {("en", "zh"): (0.12, 1.2), ("zh", "en"): (0.8, 6.0)}.get((src_lang, tgt_lang), (0.3, 3.0))
    if len(src) > 30 and not lo <= ratio <= hi:
        flags.append({"code": "length_ratio", "detail": round(ratio, 2)})
    return flags


# --------------------------------------------------------------------------- docx
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text, font, cjk, bold=False, size=20, color=None):
    hint = ' w:hint="eastAsia"' if CJK_RE.search(text) else ""
    rpr = (f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}" w:eastAsia="{cjk}"{hint}/>'
           + ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "")
           + f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>')
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _p(text, font, cjk, **kw):
    return f"<w:p>{_run(text, font, cjk, **kw)}</w:p>"


def _cell(inner, width, shade=None):
    sh = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
    return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{sh}</w:tcPr>{inner}</w:tc>'


def build_docx(path, title, header_lines, rows, font, cjk):
    widths = (1500, 3900, 3900, 1600)
    body = [_p(title, font, cjk, bold=True, size=30)] + [_p(h, font, cjk, size=18, color="555555") for h in header_lines]
    head = "".join(_cell(_p(t, font, cjk, bold=True, size=18), w, "D9E2F3")
                   for t, w in zip(("段号 / 定位 ID·locator", "原文 Source", "译文 Target", "核对 Checks"), widths))
    trs = [f"<w:tr><w:trPr><w:tblHeader/></w:trPr>{head}</w:tr>"]
    for r in rows:
        shade = "FDECEA" if r["flags"] else None
        cells = [_p(r["id"], font, cjk, bold=True, size=16) + _p(r["locator"], font, cjk, size=14, color="666666"),
                 _p(r["src"], font, cjk, size=18), _p(r["tgt"] or "【未译 UNTRANSLATED】", font, cjk, size=18),
                 "".join(_p(f, font, cjk, size=14, color="B00020") for f in r["flags"]) or _p("OK", font, cjk, size=14, color="2E7D32")]
        trs.append("<w:tr>" + "".join(_cell(c, w, shade) for c, w in zip(cells, widths)) + "</w:tr>")
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    tbl = (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>'
           + "".join(f'<w:{e} w:val="single" w:sz="4" w:color="999999"/>' for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
           + f"</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{''.join(trs)}</w:tbl>")
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{NS_W}"><w:body>'
           f'{"".join(body)}{tbl}<w:p/><w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
           f'<w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="850"/></w:sectPr></w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct); zf.writestr("_rels/.rels", rels); zf.writestr("word/document.xml", doc)


# --------------------------------------------------------------------------- render
def find_soffice():
    for c in (shutil.which("soffice"), shutil.which("libreoffice"),
              "/Applications/LibreOffice.app/Contents/MacOS/soffice", "/usr/lib/libreoffice/program/soffice",
              "/opt/libreoffice/program/soffice"):
        if c and os.path.isfile(c):
            return c
    return None


def font_available(name: str) -> bool | None:
    fc = shutil.which("fc-list")
    if fc:
        import subprocess
        try:
            out = subprocess.run([fc, ":", "family"], capture_output=True, text=True, timeout=20).stdout
            return name.lower() in out.lower()
        except Exception:
            return None
    roots = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/Library/Fonts"), "/Library/Fonts",
             "/System/Library/Fonts", "/Applications/LibreOffice.app/Contents/Resources/fonts"]
    key = name.lower().replace(" ", "")
    for r in roots:
        for dp, _, fs in os.walk(r):
            if any(key in f.lower().replace(" ", "").replace("-", "") for f in fs):
                return True
    return False


CJK_CANDIDATES = ("Noto Serif CJK SC", "Noto Sans CJK SC", "Source Han Serif SC", "Source Han Sans SC",
                  "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "AR PL UMing", "Droid Sans Fallback",
                  "Songti SC", "STSong", "Hiragino Sans GB", "PingFang SC", "SimSun", "Microsoft YaHei")


def pick_cjk_font(requested: str) -> tuple[str, dict | None]:
    """Keep the requested CJK font if installed, else the first installed candidate."""
    if font_available(requested) is not False:
        return requested, None
    for c in CJK_CANDIDATES:
        if font_available(c):
            return c, {"code": "cjk_font_substituted", "requested": requested, "used": c}
    return requested, {"code": "cjk_font_missing", "requested": requested,
                       "detail": "no CJK font from the candidate list is installed; Chinese may render as boxes"}


def glyph_audit(pdf_path: str, fitz, max_pages: int = 12) -> list:
    """Structured warnings for characters that the preview does not actually draw.

    Subset fonts carry no usable cmap, so has_glyph() is unreliable. Each
    character's box (rawdict, padded vertically for descenders such as '_') is
    checked on a grayscale raster: a non-space character with no ink in its box
    is reported as a missing glyph (rendered blank / as a box).
    """
    doc = fitz.open(pdf_path)
    missing, total = {}, 0
    for pno, pg in enumerate(doc):
        if pno >= max_pages:
            break
        pix = pg.get_pixmap(dpi=100, colorspace=fitz.csGRAY)
        sc, w, h, sm = 100 / 72.0, pix.width, pix.height, pix.samples
        for blk in pg.get_text("rawdict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln["spans"]:
                    for c in sp["chars"]:
                        ch = c["c"]
                        if not (ch.isalnum() or CJK_RE.match(ch)):  # punctuation like '_' sits outside its box
                            continue
                        total += 1
                        x0, y0, x1, y1 = c["bbox"]
                        pad = (y1 - y0) * 0.35
                        X0, Y0 = max(0, int(x0 * sc)), max(0, int((y0 - pad) * sc))
                        X1, Y1 = min(w, int(x1 * sc) + 1), min(h, int((y1 + pad) * sc) + 1)
                        if not any(sm[y * w + x] < 160 for y in range(Y0, Y1) for x in range(X0, X1)):
                            missing.setdefault(sp["font"].split("+")[-1], set()).add(ch)
    return [{"code": "missing_glyph", "font": fn, "chars": "".join(sorted(chs))[:60], "count": len(chs),
             "checked_chars": total,
             "detail": "these characters are not drawn in the preview (blank/box); install a font covering them "
                       "(e.g. Noto Serif CJK SC) or pass --cjk-font. The editable .docx text itself is intact."}
            for fn, chs in missing.items()]


def _rasterise(rep, pdf, stem, outdir, fitz):
    doc = fitz.open(pdf)
    rep["fonts_embedded"] = sorted({f[3] for pg in doc for f in pg.get_fonts()})
    rep["png"], rep["pages"] = [], len(doc)
    for old in outdir.glob(f"{stem}-p*.png"):
        old.unlink()
    for i, pg in enumerate(doc, 1):
        if i > 12:
            rep["warnings"].append({"code": "preview_truncated", "detail": f"PNG for first 12 of {len(doc)} pages; PDF is complete"})
            break
        out = outdir / f"{stem}-p{i}.png"
        pg.get_pixmap(dpi=110).save(str(out))
        rep["png"].append(str(out))


def render(docx: Path, outdir: Path, font: str, cjk: str, timeout: int = 90) -> dict:
    """docx -> PDF -> PNG. soffice first; if it is absent, fails, or drops glyphs
    (checked by glyph_audit, not assumed), re-render with PyMuPDF Story, whose built-in
    CJK fallback font covers Chinese. Every decision is a structured warning; never raises."""
    import subprocess
    import tempfile
    rep = {"ok": False, "engine": None, "pdf": None, "png": [], "warnings": []}
    av = font_available(font)
    if av is False:
        rep["warnings"].append({"code": "font_missing", "font": font,
                                "detail": "requested Latin font not installed; renderer substitutes a metric-compatible fallback if it has one"})
    try:
        import fitz  # type: ignore
    except Exception:
        fitz = None
        rep["warnings"].append({"code": "pymupdf_absent", "detail": "no PyMuPDF: no PNG rasterisation and no glyph audit"})
    soffice = find_soffice()
    pdf = outdir / (docx.stem + ".pdf")
    if soffice:
        with tempfile.TemporaryDirectory() as prof:
            try:
                p = subprocess.run([soffice, "--headless", "--norestore", f"-env:UserInstallation=file://{prof}",
                                    "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
                                   capture_output=True, text=True, timeout=timeout)
                if p.returncode == 0 and pdf.is_file():
                    rep.update(engine="soffice", pdf=str(pdf))
                else:
                    rep["warnings"].append({"code": "soffice_failed", "detail": (p.stderr or p.stdout)[-300:]})
            except Exception as exc:
                rep["warnings"].append({"code": "soffice_failed", "detail": str(exc)[:300]})
    else:
        rep["warnings"].append({"code": "soffice_absent", "detail": "no LibreOffice found; using PyMuPDF Story renderer"})
    if fitz is not None and rep["pdf"]:
        try:
            lost = glyph_audit(rep["pdf"], fitz)
        except Exception as exc:
            lost = []
            rep["warnings"].append({"code": "glyph_audit_failed", "detail": str(exc)[:200]})
        if lost:
            rep["warnings"] += [dict(w, renderer="soffice", action="re-rendered with pymupdf-story") for w in lost]
            rep["pdf"] = None
    if not rep["pdf"] and fitz is not None:
        try:
            fallback_pdf(docx, pdf, fitz, font)
            rep.update(engine="pymupdf-story", pdf=str(pdf))
            lost = glyph_audit(str(pdf), fitz)
            rep["warnings"] += [dict(w, renderer="pymupdf-story") for w in lost]
        except Exception as exc:
            rep["warnings"].append({"code": "fallback_pdf_failed", "detail": str(exc)[:300]})
    if rep["pdf"] and fitz is not None:
        try:
            _rasterise(rep, rep["pdf"], docx.stem, outdir, fitz)
            if not any(font.replace(" ", "").lower() in u.replace(" ", "").lower() for u in rep["fonts_embedded"]):
                rep["warnings"].append({"code": "font_substituted", "font": font, "embedded": rep["fonts_embedded"][:8]})
        except Exception as exc:
            rep["warnings"].append({"code": "rasterise_failed", "detail": str(exc)[:300]})
    rep["ok"] = bool(rep["pdf"] and rep["png"])
    rep["glyphs_ok"] = rep["ok"] and not any(w["code"] == "missing_glyph" and w.get("renderer") == rep["engine"] for w in rep["warnings"])
    if not rep["ok"]:
        rep["warnings"].append({"code": "no_preview", "detail": "no renderer produced PDF+PNG; editable .docx/.md are still valid"})
    return rep


def fallback_pdf(docx: Path, pdf: Path, fitz, font: str = "Liberation Serif") -> None:
    """Render the bilingual docx (title, header lines, table, red flagged rows) via PyMuPDF Story."""
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    body = xml.split("<w:tbl>")[0]
    paras = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)) for p in re.findall(r"<w:p>(.*?)</w:p>", body, flags=re.S)]
    html = []
    if paras:
        html.append(f"<h3>{paras[0]}</h3>")
        html += [f"<p style='color:#555;font-size:7pt;margin:0'>{x}</p>" for x in paras[1:]]
    html.append("<table border='1' cellspacing='0' cellpadding='3' style='font-size:7.5pt;width:100%'>")
    for i, r in enumerate(re.findall(r"<w:tr>(.*?)</w:tr>", xml, flags=re.S)):
        bg = "#D9E2F3" if i == 0 else ("#FDECEA" if "FDECEA" in r else "#FFFFFF")
        cells = ["<br/>".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", c, flags=re.S)) for c in re.findall(r"<w:tc>(.*?)</w:tc>", r, flags=re.S)]
        html.append(f"<tr style='background-color:{bg}'>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    html.append("</table>")
    css = f"body,td,p,h3{{font-family:'{font}',serif}}"
    story = fitz.Story(html="".join(html), user_css=css)
    writer = fitz.DocumentWriter(str(pdf))
    rect = fitz.paper_rect("a4-l")
    where = rect + (36, 36, -36, -36)
    more = True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    try:  # embed only the used glyphs (full CJK fallback font is several MB)
        d = fitz.open(str(pdf)); d.subset_fonts()
        tmp = str(pdf) + ".tmp"; d.save(tmp, garbage=4, deflate=True); d.close(); os.replace(tmp, str(pdf))
    except Exception:
        pass


# --------------------------------------------------------------------------- assemble
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def norm_translations(raw) -> dict:
    out = {}
    items = raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [])
    for k, v in items:
        if isinstance(v, str):
            out[k] = {"tgt": v}
        elif isinstance(v, dict):
            out[k] = {"tgt": v.get("tgt") or v.get("target") or "", "note": v.get("note"),
                      "human_check": bool(v.get("human_check"))}
    return out


def cmd_assemble(a) -> int:
    t0 = time.time()
    a.cjk_font, cjk_note = pick_cjk_font(a.cjk_font)
    work, outdir = Path(a.work), Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    case, case_err = {}, None
    try:
        case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    except Exception as exc:
        case_err = f"{type(exc).__name__}: {exc}"
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    case_id = case.get("case_id") or "not_reported"
    segdoc = load_json(work / "segments.json", None)
    limitations, uncertainties = [], []
    if case_err:
        limitations.append("case.json unreadable: " + case_err)
    if segdoc is None:
        limitations.append("no segments.json: source material was not found or not segmented")
        segs, src_lang, tgt_lang = [], "unknown", "unknown"
    else:
        segs, src_lang, tgt_lang = segdoc["segments"], segdoc["src_lang"], segdoc["tgt_lang"]
    trans = norm_translations(load_json(work / "translations.json", {}))
    glossary = load_json(work / "glossary.json", {})
    if not isinstance(glossary, dict):
        limitations.append("glossary.json is not an object; ignored"); glossary = {}

    rows, facts, evidence, flagged = [], [], [], []
    for s in segs:
        t = trans.get(s["id"], {})
        tgt = t.get("tgt") or (s["src"] if s["keep_verbatim"] else "")
        flags = check_segment(s, tgt, src_lang, tgt_lang, glossary)
        if t.get("human_check"):
            flags.append({"code": "translator_flag", "detail": t.get("note") or "translator asked for review"})
        rows.append({"id": s["id"], "locator": s["locator"], "src": s["src"], "tgt": tgt,
                     "flags": [f"{f['code']}: {json.dumps(f['detail'], ensure_ascii=False)}" for f in flags]})
        if flags:
            flagged.append({"id": s["id"], "locator": s["locator"], "flags": flags})
        for num in tc._tokens(s["src"]):
            facts.append({"fact": num, "kind": "number", "source": s["locator"], "segment": s["id"],
                          "preserved_in_target": num in tc._tokens(tgt), "changed": num not in tc._tokens(tgt)})
        for mk in _markers(s["src"]):
            facts.append({"fact": mk, "kind": "citation", "source": s["locator"], "segment": s["id"],
                          "preserved_in_target": mk in _markers(tgt), "changed": mk not in _markers(tgt)})
        for st, tt in glossary.items():
            if st.lower() in s["src"].lower():
                facts.append({"fact": f"{st} → {tt}", "kind": "term", "source": s["locator"], "segment": s["id"],
                              "preserved_in_target": tt in tgt, "changed": tt not in tgt})
        if s["kind"] in VERBATIM_KINDS:
            facts.append({"fact": s["src"][:120], "kind": s["kind"], "source": s["locator"], "segment": s["id"],
                          "preserved_in_target": tgt.strip() == s["src"].strip(), "changed": tgt.strip() != s["src"].strip()})
        evidence.append({"segment": s["id"], "locator": s["locator"], "kind": s["kind"], "source_quote": s["src"],
                         "target": tgt, "checks": "ok" if not flags else [f["code"] for f in flags],
                         "deliverable_ref": f"bilingual.docx row {s['id']}"})

    n_total, n_done = len(segs), sum(1 for r in rows if r["tgt"].strip())
    ext = "translation" if segs else "none"
    header = [f"case_id: {case_id}", f"source: {segdoc['source'] if segdoc else 'not_reported'}  ({src_lang} → {tgt_lang})",
              f"segments: {n_total}, translated: {n_done}, flagged for review: {len(flagged)}",
              "Red rows = machine check raised a flag; see the Checks column and result.json change_log."]
    md = [f"# Bilingual alignment — {case_id}", "", *[f"> {h}" for h in header], ""]
    for r in rows:
        md += [f"### {r['id']} · `{r['locator']}`", "", f"**SRC** {r['src']}", "", f"**TGT** {r['tgt'] or '【未译 UNTRANSLATED】'}", ""]
        if r["flags"]:
            md += [f"- ⚠ {f}" for f in r["flags"]] + [""]
    deliver = {}
    if segs:
        docx = outdir / "bilingual.docx"
        build_docx(docx, f"Bilingual alignment / 双语对照 — {case_id}", header, rows, a.font, a.cjk_font)
        (outdir / "bilingual.md").write_text("\n".join(md), encoding="utf-8")
        (outdir / "target.md").write_text("\n\n".join(r["tgt"] for r in rows if r["tgt"]), encoding="utf-8")
        rep = {"ok": False, "skipped": True, "warnings": []} if a.no_render else render(docx, outdir, a.font, a.cjk_font)
        if cjk_note: rep["warnings"].insert(0, cjk_note)
        deliver = {"path": str(docx), "format": "docx (4-column bilingual table: id/locator | source | target | checks)",
                   "also": {"bilingual_md": str(outdir / "bilingual.md"), "target_only_md": str(outdir / "target.md")},
                   "preview_pdf": rep.get("pdf") or "not_reported", "preview_png": rep.get("png") or ["not_reported"],
                   "render": {k: v for k, v in rep.items() if k not in ("pdf", "png")},
                   "src_lang": src_lang, "tgt_lang": tgt_lang, "segments": n_total, "translated": n_done,
                   "editable": True}
        if not rep.get("ok"):
            limitations.append("rendered preview missing: " + json.dumps(rep.get("warnings"), ensure_ascii=False)[:300])
    else:
        # Nothing to translate: still ship an editable, rendered gap report so a human
        # sees exactly what was received and what is missing. It is NOT a translation.
        gap_rows = [{"id": "C0", "locator": "case.json#case_id", "src": str(case_id), "tgt": "原样保留 / kept verbatim", "flags": []}]
        for k in ("task_instruction", "source"):
            if k in case:
                gap_rows.append({"id": f"C{len(gap_rows)}", "locator": f"case.json#{k}",
                                 "src": f"present ({type(case[k]).__name__}, {len(json.dumps(case[k], ensure_ascii=False))} chars JSON); see case.json",
                                 "tgt": "（案例元数据，非待译正文 / case metadata, not source text）", "flags": []})
        for k, v in contract.items():
            gap_rows.append({"id": f"C{len(gap_rows)}", "locator": f"output_contract#{k}", "src": str(v),
                             "tgt": "not_reported" if k not in ("case_id",) else "filled",
                             "flags": [] if k == "case_id" else ["insufficient_evidence: no source text in case"]})
        docx = outdir / "gap_report.docx"
        build_docx(docx, f"Insufficient evidence / 证据不足 — {case_id}",
                   ["No translatable source text was found in the case or next to it.",
                    "未在案例中找到可翻译的原文；本文件只是缺口清单，不是译文。"], gap_rows, a.font, a.cjk_font)
        rep = {"ok": False, "skipped": True, "warnings": []} if a.no_render else render(docx, outdir, a.font, a.cjk_font)
        if cjk_note: rep["warnings"].insert(0, cjk_note)
        deliver = {"path": "not_reported", "reason": "no source text available to translate",
                   "gap_report_docx": str(docx), "preview_pdf": rep.get("pdf") or "not_reported",
                   "preview_png": rep.get("png") or ["not_reported"],
                   "render": {k: v for k, v in rep.items() if k not in ("pdf", "png")}, "editable": True}

    change_log = [{"action": "segment", "detail": f"{n_total} segments from {segdoc['source'] if segdoc else 'none'}",
                   "human_check": False}] if segdoc else []
    if glossary:
        change_log.append({"action": "apply_glossary", "detail": f"{len(glossary)} locked terms (work/glossary.json)", "human_check": False})
    for k, t in trans.items():
        if t.get("note"):
            change_log.append({"action": "translator_note", "segment": k, "detail": t["note"], "human_check": bool(t.get("human_check"))})
    for f in flagged:
        change_log.append({"action": "flag_for_review", "segment": f["id"], "locator": f["locator"],
                           "detail": f["flags"], "human_check": True, "resolved": False})
    if not segs:
        change_log.append({"action": "no_translation", "detail": "case carries no source text (no materials, no inline text); "
                           "nothing was translated or edited", "human_check": True, "resolved": False})
        change_log.append({"action": "write_gap_report", "detail": "gap_report.docx + rendered PDF/PNG list what was received "
                           "and which contract fields are not_reported", "human_check": False})

    if case_err or segdoc is None and not case:
        status = "input_error"
    elif not segs:
        status = "insufficient_evidence"
    elif n_done < n_total or flagged:
        status = "partial"
    else:
        status = "answered"
    if n_done < n_total:
        limitations.append(f"{n_total - n_done} segment(s) untranslated; see change_log/flag_for_review")
    if flagged:
        uncertainties.append(f"{len(flagged)} segment(s) carry machine-check flags; flags are heuristics (e.g. 万/亿 conversions, "
                             "paraphrased hedges) and need a human decision, they are not proven errors")
    for w in (segdoc or {}).get("reader", {}).get("warnings", []):
        uncertainties.append(w)

    result = {"case_id": case_id}
    if case_id == "EXAMPLE-NOT-A-TEST-CASE":
        result["example_run"] = True
    filled = {"deliverable": deliver, "preserved_facts": facts or [{"fact": "not_reported", "reason": "no numbers/citations/terms in source or no source"}],
              "evidence_map": evidence or [{"claim": "not_reported", "locator": "not_reported", "reason": "no source segments"}],
              "change_log": change_log, "status": status}
    for key in list(contract) + ["deliverable", "preserved_facts", "evidence_map", "change_log", "status"]:
        if key == "case_id" or key in result:
            continue
        result[key] = filled.get(key, {"value": "not_reported", "contract_says": contract.get(key),
                                       "reason": "field not produced by the bilingual pipeline; author it or leave this explanation"})
    result["limitations"] = limitations or ["none beyond the machine-check heuristics listed in uncertainties"]
    if not segs and not case_err:
        result["limitations"].append("human takeover: supply the source text (file next to case.json or an inline "
                                     "source_text field) and rerun segment + assemble")
    result["uncertainties"] = uncertainties or [
        {"item": "not_reported", "reason": "no segments to judge" if not segs else "no machine-check flags raised; "
         "checks are heuristic, so a clean run is not proof of a correct translation"}]
    result["summary"] = {"segments": n_total, "translated": n_done, "flagged": len(flagged),
                         "flag_counts": dict(Counter(f["code"] for x in flagged for f in x["flags"])),
                         "glossary_terms": len(glossary), "elapsed_s": round(time.time() - t0, 2)}
    (outdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"status": status, **result["summary"], "deliverable": deliver.get("path"),
                      "preview_pdf": deliver.get("preview_pdf"), "preview_png": len([p for p in deliver.get("preview_png", []) if p != "not_reported"]),
                      "render_warnings": (deliver.get("render") or {}).get("warnings")}, ensure_ascii=False))
    return 2 if status == "input_error" else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("segment")
    s.add_argument("--source", required=True); s.add_argument("--work", default="/app/work")
    s.add_argument("--lines", help="A-B 1-based inclusive line range for text sources")
    s.add_argument("--src-lang", default="auto", choices=["auto", "en", "zh"])
    s.add_argument("--tgt-lang", default="auto", choices=["auto", "en", "zh"])
    s.add_argument("--append", action="store_true", help="add to an existing work/segments.json (multi-file cases)")
    s.add_argument("--translate-refs", action="store_true", help="translate reference entries instead of keeping verbatim")
    m = sub.add_parser("assemble")
    m.add_argument("--case", default="/input/case.json"); m.add_argument("--work", default="/app/work")
    m.add_argument("--outdir", default="/app/output"); m.add_argument("--font", default="Liberation Serif")
    m.add_argument("--cjk-font", default="Noto Serif CJK SC"); m.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    return cmd_segment(a) if a.cmd == "segment" else cmd_assemble(a)


if __name__ == "__main__":
    sys.exit(main())
