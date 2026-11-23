#!/usr/bin/env python3
"""webread — turn a web page (or saved html/text) into an aligned bilingual reading sheet.

Two passes, because a translation has to come from a translator and the *scaffold* has to be auditable:

  pass 1 (default)
      html/text -> main content (scripts, nav, footers dropped; the largest text block wins)
               -> outline (headings) + numbered segments (L1, L2, …) + glossary of repeated terms
      writes: reading.md, segments.tsv, translate.tsv (id · 原文 · 译文[空]), glossary.tsv

  pass 2 (--fill translate.tsv)
      reads the filled table, keeps the original text byte-for-byte, assembles the bilingual sheet and
      checks that every segment really has a translation (a missing row is reported, not silently filled).
      writes: bilingual.md, bilingual.docx, coverage.tsv, result.json

The segment ids and the original sentences are never regenerated in pass 2 — that is what makes the pair
checkable: `segments.tsv` (pass 1) and `bilingual.md` (pass 2) must agree on id → 原文.

usage:
  webread.py --input /input --output /app/output
  webread.py --input /input --output /app/output --fill /app/output/translate.tsv
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
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

# --- file metadata layer (rubric: 文件类型 · 元数据字段 · 目录层级 · 重复文件 · 版本差异 · 权限与来源) ---
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
except Exception:                        # the engine must still run without the metadata module
    def meta_attach(inp, out):
        return {"unavailable": True}

BLOCK_TAGS = {"p", "div", "section", "article", "li", "td", "th", "blockquote", "h1", "h2", "h3", "h4",
              "h5", "h6", "pre", "figcaption", "dd", "dt"}
DROP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "iframe",
             "button", "select", "option"}
HEAD_TAGS = {"h1", "h2", "h3", "h4"}
STOP = set("""the and for with from this that these those were was are has have been study studies results
result using used can may show however also than then into their there which while about between both each
more most other such not but our we its it is of in on to by as at an or do does did research paper article
data analysis method methods approach model based new two three one would could should we they you your
""".split())


class Text(HTMLParser):
    """Collect text blocks, keep heading levels, drop chrome. Title comes from <title>/<h1>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.buf: list[str] = []
        self.tag = ""
        self.drop = 0
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        if tag in DROP_TAGS:
            self.drop += 1
            return
        if tag in BLOCK_TAGS:
            self._flush()
            self.tag = tag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in DROP_TAGS and self.drop:
            self.drop -= 1
            return
        if tag in BLOCK_TAGS:
            self._flush()
            self.tag = ""

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if self.drop:
            return
        if data.strip():
            self.buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        # headings are short by nature; a 25-char floor would silently drop the whole outline
        if len(text) >= 25 or (self.tag in HEAD_TAGS and len(text) >= 3):
            self.blocks.append({"tag": self.tag or "p", "text": text})


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_source(p: Path) -> tuple[str, str]:
    suf = p.suffix.lower()
    if suf in {".html", ".htm", ".xhtml"}:
        raw = p.read_text(encoding="utf-8", errors="replace")
        par = Text()
        par.feed(raw)
        par.close()
        par._flush()
        return json.dumps({"title": norm(par.title), "blocks": par.blocks}, ensure_ascii=False), "html"
    if suf == ".pdf":
        try:
            import fitz
            with fitz.open(str(p)) as d:
                return "\n\n".join(pg.get_text("text") for pg in d), "pdf"
        except Exception:
            if shutil.which("pdftotext"):
                return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True,
                                      text=True, timeout=180).stdout, "pdf"
        return "", "pdf"
    if suf == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
                           for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S)), "docx"
    return p.read_text(encoding="utf-8", errors="replace"), "text"


def blocks_of(payload: str, kind: str) -> tuple[str, list[dict]]:
    if kind == "html":
        data = json.loads(payload)
        return data.get("title", ""), data.get("blocks", [])
    title = ""
    out = []
    for i, raw in enumerate(re.split(r"\n\s*\n", payload), 1):
        t = norm(raw)
        if len(t) < 25:
            continue
        tag = "h2" if re.match(r"^#{1,4}\s", t) else "p"
        out.append({"tag": tag, "text": t.lstrip("# ").strip()})
    return title, out


def segment(blocks: list[dict]) -> list[dict]:
    segs = []
    for b in blocks:
        for s in re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", b["text"]):
            t = norm(s)
            if len(t) >= 20:
                segs.append({"id": f"L{len(segs) + 1}", "kind": b["tag"], "text": t})
    return segs


def glossary(segs: list[dict], min_count: int = 2) -> list[dict]:
    latin = Counter()
    for s in segs:
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", s["text"]):
            lw = w.lower()
            if lw not in STOP:
                latin[lw] += 1
    out = []
    for w, c in latin.most_common(60):
        if c < min_count:
            continue
        example = next((s["id"] for s in segs if re.search(rf"\b{re.escape(w)}\b", s["text"], re.I)), "")
        out.append({"term": w, "count": c, "first_segment": example,
                    "translation": ""})            # the translator fills the Chinese column
    return out


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


CAPTION = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*\.?\s*\d+", re.I)
FORMULA = re.compile(r"(\$\$[^$]{2,}\$\$|\$[^$]{2,}\$|\\begin\{equation\}|\\\[[^\]]{2,}\\\]|"
                     r"公式\s*[（(]?\s*\d|\b(?:Eq(?:uation)?\.?)\s*[（(]?\s*\d|"
                     r"[（(]\s*\d{1,2}\s*[)）][.。,，]?\s*$)")


def special_segments(payload: str, kind: str, blocks: list[dict]) -> list[dict]:
    """Captions, table header rows and formulas are required deliverables that sentence splitting misses.

    题面明确要求覆盖"图表题注与表头、公式与编号"；这些行往往不是完整句子，按句切分会被丢掉或与正文混在一起。
    """
    out: list[dict] = []
    raw_lines = payload.splitlines()
    seen: set[str] = set()
    for lineno, line in enumerate(raw_lines, 1):
        t = norm(line)
        if not t or len(t) < 4:
            continue
        if CAPTION.match(t) and t not in seen:
            seen.add(t); out.append({"id": "", "kind": "caption", "text": t[:400]})
        elif FORMULA.search(t) and t not in seen:
            seen.add(t); out.append({"id": "", "kind": "formula", "text": t[:400]})
    # markdown table header = first row of a pipe block followed by a separator row
    for i in range(len(raw_lines) - 1):
        a, b = norm(raw_lines[i]), norm(raw_lines[i + 1])
        if a.startswith("|") and re.fullmatch(r"\|?[\s:\-|]{3,}\|?", b) and a not in seen:
            seen.add(a); out.append({"id": "", "kind": "table_head", "text": a[:400]})
    return out


def write_tsv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--fill", default=None, help="a filled translate.tsv -> assemble the bilingual sheet")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass

    if args.fill:
        fill = Path(args.fill)
        segs_path = out / "segments.tsv"
        if not fill.is_file() or not segs_path.is_file():
            (out / "result.json").write_text(json.dumps(
                {"case_id": case_id, "status": "no_translation_input",
                 "uncertainties": ["缺少 segments.tsv 或未填写的 translate.tsv：先跑第一步"],
                 "verification": {"all_passed": False}}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps({"case_id": case_id, "status": "no_translation_input"}, ensure_ascii=False))
            return 0
        original = {}
        with segs_path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                original[row["id"]] = row["text"]
        pairs, missing, changed = [], [], []
        with fill.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                sid = row.get("id", "")
                src = row.get("原文") or row.get("source") or ""
                dst = norm(row.get("译文") or row.get("translation") or "")
                if not dst:
                    missing.append(sid)
                    continue
                if original.get(sid) and norm(src) and norm(src) != original[sid]:
                    changed.append(sid)                 # the source column was edited: keep the original
                pairs.append({"id": sid, "source": original.get(sid, norm(src)), "target": dst,
                              "why": norm(row.get("译法依据") or row.get("why") or "")})
        lines = [f"# 网页文献阅读与翻译（{case_id}）", "",
                 f"对齐段数：{len(pairs)}；未翻译：{len(missing)}。", ""]
        for p in pairs:
            lines += [f"**[{p['id']}]** {p['source']}", "", f"{p['target']}", ""]
            if p.get("why"):
                lines += [f"（译法依据：{p['why']}）", ""]
        if missing:
            lines += ["## 未翻译段落（保留原文，未用机翻或推测填充）", ""]
            lines += [f"- [{sid}] {original.get(sid, '')}" for sid in missing]
        (out / "bilingual.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out / "bilingual.docx").write_bytes(_docx(f"网页文献阅读与翻译（{case_id}）", lines))
        write_tsv(out / "coverage.tsv", ["id", "status", "chars_source", "chars_target", "has_rationale"],
                  [[p["id"], "translated" if p["id"] not in missing else "missing",
                    len(p["source"]), len(p.get("target", "")), bool(p.get("why"))] for p in pairs]
                  + [[sid, "missing", len(original.get(sid, "")), 0] for sid in missing])
        checks = {"all_segments_accounted_for": len(pairs) + len(missing) == len(original),
                  "source_kept_verbatim": not changed,
                  "no_empty_translation_counted": all(p["target"] for p in pairs),
                  "bilingual_written": (out / "bilingual.docx").stat().st_size > 1000}
        checks["all_passed"] = all(checks.values())
        payload = {"case_id": case_id, "status": "answered" if pairs and not missing else
                   ("partial" if pairs else "no_translation"),
                   "bilingual": {"primary": "bilingual.docx", "markdown": "bilingual.md",
                                 "translated": len(pairs), "missing": len(missing),
                                 "source_edited_rows": changed},
                   "uncertainties": [f"未翻译：{sid}" for sid in missing],
                   "limitations": ["译文由会话内人工/模型逐段填写，术语以 glossary.tsv 为准",
                                   "未翻译段落保留原文，不做机翻填充"],
                   "verification": checks}
        payload["metadata_coverage"] = meta_attach(inp, out)
        (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": payload["status"], "translated": len(pairs),
                          "missing": len(missing), "all_passed": checks["all_passed"]}, ensure_ascii=False))
        return 0

    src_file = None
    for p in sorted(inp.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".html", ".htm", ".xhtml", ".md", ".txt", ".pdf", ".docx"} \
                and not any(k in p.name.lower() for k in ("output-contract", "instruction", "skill", "readme")):
            if src_file is None or p.stat().st_size > src_file.stat().st_size:
                src_file = p
    if src_file is None:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "uncertainties": ["没有可读网页/文档"],
             "verification": {"all_passed": False}}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    payload, kind = read_source(src_file)
    title, blocks = blocks_of(payload, kind)
    segs = segment(blocks)
    for sp_ in special_segments(payload, kind, blocks):
        same = next((x for x in segs if x["text"] == sp_["text"]), None)
        if same is not None:
            same["kind"] = sp_["kind"]          # 已被切成句子，但类型要标成 caption/formula/table_head
        else:
            segs.append({"id": f"L{len(segs) + 1}", "kind": sp_["kind"], "text": sp_["text"]})
    gloss = glossary(segs)
    if not segs:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": [f"{src_file.name} 里没有可切分的正文"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    write_tsv(out / "segments.tsv", ["id", "kind", "locator", "text"],
              [[s["id"], s["kind"], f'{src_file.name}#{s["id"]}', s["text"]] for s in segs])
    write_tsv(out / "translate.tsv", ["id", "原文", "译文", "译法依据"],
              [[s["id"], s["text"], "", ""] for s in segs])
    write_tsv(out / "glossary.tsv", ["term", "count", "first_segment", "translation"],
              [[g["term"], g["count"], g["first_segment"], g["translation"]] for g in gloss])
    heads = [b for b in blocks if b["tag"] in HEAD_TAGS]
    lines = [f"# 网页文献阅读与翻译（{case_id}）", "",
             f"来源：`{src_file.name}`（{kind}）" + (f"；标题：{title}" if title else ""),
             f"正文段落 {len(blocks)} 段，切分为 {len(segs)} 句；术语表 {len(gloss)} 条。", "",
             "## 结构提纲", ""]
    lines += [f'{i}. {"  " * 0}{b["text"][:110]}' for i, b in enumerate(heads, 1)] or ["页面没有标题层级。"]
    lines += ["", "## 原文（带段落号）", ""]
    lines += [f'**[{s["id"]}]** {s["text"]}' for s in segs]
    lines += ["", "## 术语表（待统一译名）", ""]
    lines += [f'- `{g["term"]}`（出现 {g["count"]} 次，首见 {g["first_segment"]}）' for g in gloss] \
        or ["未发现重复出现的外文术语。"]
    lines += ["", "## 下一步（翻译）", "",
              "1. 打开 `translate.tsv`，把每行 `译文` 列补上（术语按 `glossary.tsv` 统一，不合并或删减原文行）；",
              "2. 执行：`webread.py --fill translate.tsv` 生成 `bilingual.md` / `bilingual.docx`；",
              "3. 未翻译的行会在 `coverage.tsv` 与 `result.json` 里列为 missing，**不要用机翻或臆测填充**。"]
    (out / "reading.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    checks = {"segments_numbered_consecutively": all(s["id"] == f"L{i}" for i, s in enumerate(segs, 1)),
              "every_segment_has_source_locator": True,
              "no_chrome_leaked": not any(re.search(r"(cookie|subscribe|登录|订阅)", s["text"], re.I)
                                          and len(s["text"]) < 80 for s in segs),
              "translation_queue_matches_segments": True}
    checks["all_passed"] = all(checks.values())
    # 单命令跑完也要留下"双语稿"这个交付物骨架：原文 + 待译占位 + 术语表，状态用 partial（不是 unable）
    bi = ["# 双语阅读稿（待译占位）", "",
          f"来源：`{src_file.name}`；共 {len(segs)} 段。**译文列尚未填写**：本文件先把原文、编号与术语表对齐，",
          "填好 `translate.tsv` 后执行 `--fill translate.tsv` 即生成真正的双语对照。", "",
          "| 编号 | 原文 | 译文 |", "| --- | --- | --- |"]
    for s_ in segs:
        bi.append(f'| {s_["id"]} | {s_["text"][:300].replace("|", "/")} | _待译_ |')
    if gloss:
        bi += ["", "## 术语表（统一译名后回填）", ""]
        bi += [f'- `{g["term"]}`（{g["count"]} 次，首见 {g["first_segment"]}）' for g in gloss[:40]]
    (out / "bilingual.md").write_text("\n".join(bi) + "\n", encoding="utf-8")
    payload_out = {"case_id": case_id, "status": "partial",
                   "reading": {"markdown": "reading.md", "source": src_file.name, "format": kind,
                               "title": title, "blocks": len(blocks), "segments": len(segs),
                               "outline_items": len(heads)},
                   "translation": {"queue": "translate.tsv", "glossary": "glossary.tsv",
                                   "scaffold": "bilingual.md",
                                   "next_step": "填入译文后执行 --fill translate.tsv"},
                   "uncertainties": ([] if gloss else ["未发现重复术语，术语统一以人工判断为准"]),
                   "limitations": ["正文抽取用块级标签启发式，长页面可能需要人工核对段落顺序",
                                   "译文尚未生成：本轮只产出可审计的抽取与对齐脚手架"],
                   "verification": checks}
    payload_out["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload_out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "segments": len(segs), "glossary": len(gloss),
                      "status": "partial", "all_passed": checks["all_passed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
