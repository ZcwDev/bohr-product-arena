#!/usr/bin/env python3
"""weekly-skill 学位论文与长文档写作 —— 单一入口。

    PY=${WEEKLY_TOOLS_PYTHON:-/opt/weekly-tools/bin/python}; [ -x "$PY" ] || PY=python3
    "$PY" /skills/weekly-skill/scripts/run.py --input /input --output /app/output

一条命令完成：读契约(case.json) → 读材料(md/txt/tex/json/docx/pdf) → 切块并保留定位
→ 跨章节一致性检查(缩写/术语、引用、图表公式编号、数值冲突、结论可追溯、自述核对)
→ 对工单 target_ids 逐条起草有原文引语的编辑注释 → 逐条接受/拒绝 → v0/v1 版本对比、
回滚与重放、受控非法操作(状态不变) → 写可编辑稿(.md + .docx) + 真实渲染预览(PDF/PNG)
→ result.json（契约字段运行时从输入读取，缺值写 not_reported）。

依赖分两条路：/opt/weekly-tools venv(fitz/docx/matplotlib) + 系统 soffice/pdftoppm 为主路径；
缺任何一个都降级到标准库(zipfile 手写 OOXML / zlib 解 PDF Flate / 手写 PDF)，不抛异常退出。
可选 --annotations FILE：用 Agent 精修过的注释文本替换自动草稿（引语必须逐字存在于原文，否则拒绝）。
"""
from __future__ import annotations
# --- evaluator-environment bootstrap (injected) ---
# The replay image keeps the science stack (fitz / matplotlib / python-docx /
# python-pptx / playwright / CairoSVG) inside its own venv and points at it with
# WEEKLY_TOOLS_PYTHON; a bare `python3` is miniconda's and may not have them, which
# is how a run ends up with no rendered previews and no PDF parsing. Re-exec under
# that interpreter when it is present, and stay put when it is not.
import os as _os, sys as _sys
_TOOLS_PY = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
if (_os.path.exists(_TOOLS_PY) and not _os.environ.get("_SKILL_REEXEC")
        and _os.path.realpath(_sys.executable) != _os.path.realpath(_TOOLS_PY)):
    _os.environ["_SKILL_REEXEC"] = "1"
    try:
        _os.execv(_TOOLS_PY, [_TOOLS_PY, _os.path.abspath(__file__)] + _sys.argv[1:])
    except OSError:
        pass
# --- end evaluator-environment bootstrap ---


def _reexec_under_weekly_tools():
    """评测镜像的科学栈(fitz/docx/matplotlib)在 WEEKLY_TOOLS_PYTHON 指向的 venv 里；PATH 上的 python3 可能没有。
    只在作为脚本运行时调用（被 selfcheck import 时不 exec）。解释器不存在/不可执行就原地继续（降级路径）。"""
    import os as _os, sys as _sys
    py = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
    if (_os.path.exists(py) and _os.access(py, _os.X_OK) and not _os.environ.get("_SKILL_REEXEC")
            and _os.path.realpath(_sys.executable) != _os.path.realpath(py)):
        _os.environ["_SKILL_REEXEC"] = "1"
        try:
            _os.execv(py, [py, _os.path.abspath(__file__)] + _sys.argv[1:])
        except OSError:
            pass

import argparse
import copy
import csv
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
import zlib
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="mpl-"))
SKILL_VERSION = "2.0.0"
ENGINES: dict = {"python": sys.executable, "reexec_to_weekly_tools": bool(os.environ.get("_SKILL_REEXEC"))}
WARNINGS: list = []


FORCE_STDLIB = os.environ.get("SKILL_FORCE_STDLIB") == "1"  # 维护者用：强制走纯标准库降级路径，验证降级产物


def opt_import(name):
    if FORCE_STDLIB:
        return None
    try:
        return __import__(name, fromlist=["_"])
    except Exception:  # ImportError and broken native libs alike -> degrade
        return None


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------- input / contract
CASE_NAMES = ("case.json", "input.json", "example-input.json")


def find_case(inp: Path):
    for n in CASE_NAMES:
        p = inp / n
        if p.is_file():
            return p
    for p in sorted(inp.rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "case_id" in d:
                return p
        except Exception:
            continue
    return None


def merge_workorder(case: dict, inp: Path) -> None:
    """organizer-edit-workorder.json 可能单独提供；合并进 case（不覆盖已有值）。"""
    for p in inp.rglob("*workorder*.json"):
        try:
            wo = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            WARNINGS.append(f"workorder unreadable {p.name}: {e}")
            continue
        cur = case.setdefault("organizer_edit_workorder", {})
        for k, v in (wo.items() if isinstance(wo, dict) else []):
            cur.setdefault(k, v)
        case.setdefault("_workorder_file", str(p))


def status_enum(case: dict):
    """契约里的状态枚举：status_values / output_contract.status 里 a|b|c 形式；没有则 None。"""
    for src in (case.get("status_values"), (case.get("output_contract") or {}).get("status_values"),
                (case.get("output_contract") or {}).get("status")):
        if isinstance(src, list) and src:
            return [str(x) for x in src]
        if isinstance(src, str) and ("|" in src or "," in src):
            vals = [x.strip(" `'\"") for x in re.split(r"[|,/]", src) if x.strip()]
            vals = [v for v in vals if re.fullmatch(r"[A-Za-z_]+", v)]
            if vals:
                return vals
    return None


def pick_status(enum, complete: bool) -> str:
    good = ["answered", "completed", "complete", "ok", "success", "done", "pass"]
    part = ["partial", "partially_answered", "partial_success"]
    if enum:
        low = {e.lower(): e for e in enum}
        for k in (good if complete else part) + (part if complete else good):
            if k in low:
                return low[k]
        return enum[0]
    return "completed" if complete else "partial"


# ----------------------------------------------------------------------------- material parsing
TEXT_EXT = {".md", ".markdown", ".txt", ".tex", ".rst"}
ID_TAG = re.compile(r"^\s*(?:\[((?=[\w.\-:]*[A-Za-z])[A-Za-z0-9][\w.\-:]{1,40})\]|\{#([\w.\-:]+)\}|<!--\s*(?:id[:=]\s*)?([\w.\-:]+)\s*-->)\s*")
HEAD = re.compile(r"^(#{1,6})\s+(.+)$|^\\(?:chapter|section|subsection)\*?\{(.+?)\}")
CAPTION = re.compile(r"^\s*(Table|Figure|Fig\.|Eq\.|表|图)\s*([0-9]+(?:[.\-][0-9]+)?)\s*[.:：]")


LINE_ID = re.compile(r"\n(?=[ \t]*\[[A-Za-z][\w.\-:]*\d[\w.\-:]*\][ \t])")
# 运行时根据工单 target_ids 推断的块号模式（如 C01-P002 → C01\-P\d+），用于识别 **C01-P002**、C01-P002:、### C01-P002 等写法
DYN = {"id": None, "line": None, "lead": None}


def set_dynamic_id_pattern(targets):
    pats = sorted({re.sub(r"\\d\+(?:\\d\+)+", r"\\d+", re.sub(r"\d+", r"\\d+", re.escape(t))) for t in targets if t})
    if not pats:
        return
    core = "(?:" + "|".join(pats) + ")"
    DYN["id"] = re.compile(core)
    DYN["lead"] = re.compile(r"^[\s>*_#\[\(\{<!\-`]*(?:id\s*[:=]\s*)?(" + core + r")(?![\w])[\]\)\}`*_]*\s*(?:-->)?\s*[:：|.\-–—]?\s*")
    DYN["line"] = re.compile(r"\n(?=[ \t>*_#\[\(\{<!\-`]*" + core + r"(?![\w]))")


def split_paragraphs(text: str):
    """空行分段；另外在“行首块号 [ABC-P012]”处强制分段（PDF 文本常无空行）。"""
    for m in re.finditer(r"(?:[^\n]|\n(?![ \t]*\n))+", text):
        para, base = m.group(0), m.start()
        cut_pos = {x.end() for x in LINE_ID.finditer(para)}
        if DYN["line"]:
            cut_pos |= {x.end() for x in DYN["line"].finditer(para)}
        cuts = [0] + sorted(cut_pos) + [len(para)]
        for a, b in zip(cuts, cuts[1:]):
            seg = para[a:b]
            if seg.strip():
                yield base + a, seg.rstrip("\n")


def blocks_from_text(text: str, fname: str, page=None, id_prefix="B"):
    blocks, chapter, n, pending_id = [], "", 0, None
    for start, para in split_paragraphs(text):
        lead = len(para) - len(para.lstrip())
        body = para.strip()
        bid, m = None, ID_TAG.match(body)
        if not m and DYN["lead"]:
            m = DYN["lead"].match(body)
        if m:
            bid = next(g for g in m.groups() if g)
            body_off = start + lead + m.end()
            body = body[m.end():]
            if not body.strip():  # 块号单独占一行（下一段才是正文）：把块号挂到下一段
                pending_id = bid
                continue
        else:
            body_off = start + lead
            if pending_id:
                bid, pending_id = pending_id, None
        h = HEAD.match(body)
        kind = "heading" if h else ("table" if body.startswith("|") else
                                    ("caption" if CAPTION.match(body) else "paragraph"))
        if h:
            chapter = (h.group(2) or h.group(3) or "").strip()
        n += 1
        blocks.append({
            "id": bid or f"{id_prefix}{n:04d}", "has_native_id": bool(bid), "file": fname, "kind": kind,
            "chapter": chapter, "text": body, "file_char_start": body_off,
            "line": text.count("\n", 0, body_off) + 1, "page": page,
        })
    return blocks


def pdf_text_pages(path: Path):
    fitz = opt_import("fitz")
    if fitz:
        try:
            doc = fitz.open(str(path))
            pages = [p.get_text("text") for p in doc]
            ENGINES.setdefault("pdf_parse", []).append("pymupdf")
            return pages
        except Exception as e:
            WARNINGS.append(f"fitz failed on {path.name}: {e}")
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120)
            if out.returncode == 0:
                ENGINES.setdefault("pdf_parse", []).append("pdftotext")
                return out.stdout.decode("utf-8", "replace").split("\f")
        except Exception as e:
            WARNINGS.append(f"pdftotext failed: {e}")
    # stdlib fallback: inflate FlateDecode streams, pull text-show operands
    raw = path.read_bytes()
    texts = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            data = zlib.decompress(m.group(1))
        except Exception:
            continue
        parts = re.findall(rb"\((.*?)(?<!\\)\)\s*Tj|\[(.*?)\]\s*TJ", data, re.S)
        s = []
        for a, b in parts:
            s.append(a.decode("latin-1") if a else "".join(x.decode("latin-1") for x in re.findall(rb"\((.*?)(?<!\\)\)", b)))
        if s:
            texts.append(" ".join(s))
    ENGINES.setdefault("pdf_parse", []).append("stdlib-zlib(degraded)")
    WARNINGS.append(f"{path.name}: PDF parsed by stdlib fallback; text order/ligatures may be imperfect")
    return ["\n\n".join(texts)]


def docx_paragraphs(path: Path):
    docx = opt_import("docx")
    if docx:
        try:
            d = docx.Document(str(path))
            out = []
            for p in d.paragraphs:
                st = (p.style.name if p.style is not None else "") or ""
                t = p.text
                if st.lower().startswith("heading") and t.strip():
                    t = "#" * min(6, int(re.sub(r"\D", "", st) or 1)) + " " + t
                out.append(t)
            for tb in d.tables:
                for r in tb.rows:
                    out.append("| " + " | ".join(c.text.strip() for c in r.cells) + " |")
            ENGINES.setdefault("docx_parse", []).append("python-docx")
            return out
        except Exception as e:
            WARNINGS.append(f"python-docx failed on {path.name}: {e}")
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        import html as _h
        t = _h.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", p, re.S)))
        if re.search(r'w:pStyle w:val="Heading(\d)', p):
            t = "#" * int(re.search(r'Heading(\d)', p).group(1)) + " " + t
        out.append(t)
    ENGINES.setdefault("docx_parse", []).append("stdlib-zipfile(degraded)")
    return out


def _xml_text(xml: str) -> str:
    import html as _h
    return _h.unescape(re.sub(r"<[^>]+>", "", xml))


def html_text(raw: str) -> str:
    """HTML → 段落文本（标准库）；h1-h6 转为 markdown 标题，保留 id="..." 作为块号。"""
    import html as _h
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw)

    def blk(m):
        tag, attrs, inner = m.group(1).lower(), m.group(2) or "", m.group(3)
        idm = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', attrs)
        t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        if not t:
            return "\n"
        pre = "#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""
        return f"\n\n{('[' + idm.group(1) + '] ') if idm and not pre else ''}{pre}{t}\n\n"
    txt = re.sub(r"(?is)<(h[1-6]|p|li|td|th|caption|figcaption|div)\b([^>]*)>(.*?)</\1>", blk, raw)
    txt = _h.unescape(re.sub(r"<[^>]+>", " ", txt))
    return re.sub(r"\n{3,}", "\n\n", txt)


def xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [_xml_text(si) for si in re.findall(r"<si>(.*?)</si>", sx, re.S)]
        out = []
        for sh in sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            out.append(f"# {Path(sh).stem}")
            x = z.read(sh).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", x, re.S):
                cells = []
                for attrs, inner in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                    v = re.search(r"<v>(.*?)</v>", inner, re.S)
                    isv = re.search(r"<is>(.*?)</is>", inner, re.S)
                    if 't="s"' in attrs and v:
                        cells.append(shared[int(v.group(1))] if int(v.group(1)) < len(shared) else "")
                    elif isv:
                        cells.append(_xml_text(isv.group(1)))
                    else:
                        cells.append(v.group(1) if v else "")
                out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
            out.append("")
    return "\n".join(out)


def pptx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
        out = []
        for i, s in enumerate(slides, 1):
            x = z.read(s).decode("utf-8", "replace")
            out.append(f"# Slide {i}")
            for p in re.findall(r"<a:p>(.*?)</a:p>", x, re.S):
                t = "".join(re.findall(r"<a:t>(.*?)</a:t>", p, re.S))
                if t.strip():
                    out.append(_xml_text(t) + "\n")
    return "\n".join(out)


def json_blocks(obj, fname):
    """JSON 材料：找 {id/block_id/paragraph_id + text/content} 形状的对象。"""
    found = []

    def walk(o, chapter=""):
        if isinstance(o, dict):
            idk = next((k for k in ("block_id", "paragraph_id", "id", "pid") if isinstance(o.get(k), str)), None)
            tk = next((k for k in ("text", "content", "paragraph", "body", "original_text") if isinstance(o.get(k), str)), None)
            ch = o.get("chapter") or o.get("section") or o.get("heading") or o.get("title") or chapter
            ch = ch if isinstance(ch, str) else chapter
            if idk and tk:
                found.append({"id": o[idk], "has_native_id": True, "file": fname, "kind": "paragraph", "chapter": ch,
                              "text": o[tk], "file_char_start": None, "line": None, "page": o.get("page"),
                              "json_path": f"{idk}={o[idk]}"})
            for k, v in o.items():
                # 形如 {"C01-P002": "段落文本"} 或 {"C01-P002": {"text": ...}}：以块号为键
                if DYN["id"] and isinstance(k, str) and DYN["id"].fullmatch(k):
                    if isinstance(v, str):
                        found.append({"id": k, "has_native_id": True, "file": fname, "kind": "paragraph", "chapter": chapter,
                                      "text": v, "file_char_start": None, "line": None, "page": None, "json_path": f"key={k}"})
                        continue
                    if isinstance(v, dict) and not any(isinstance(v.get(x), str) for x in ("block_id", "paragraph_id", "id", "pid")):
                        t2 = next((v[x] for x in ("text", "content", "paragraph", "body", "original_text") if isinstance(v.get(x), str)), None)
                        if t2:
                            found.append({"id": k, "has_native_id": True, "file": fname, "kind": "paragraph",
                                          "chapter": v.get("chapter") or v.get("section") or chapter, "text": t2,
                                          "file_char_start": None, "line": None, "page": v.get("page"), "json_path": f"key={k}"})
                if isinstance(v, (dict, list)):
                    walk(v, ch)
        elif isinstance(o, list):
            for v in o:
                walk(v, chapter)
    walk(obj)
    return found


def case_facts(cp: Path, case: dict, rel: str, max_len=240):
    """把 case.json 中的短标量（来源题名/DOI/版本/工单号/目标号…）定位到原文件字符区间。
    按文档顺序游标查找，保证重复值（如 case_id）定位到正确出现处；找不到（转义差异）则跳过，不猜。"""
    raw = cp.read_text(encoding="utf-8")
    out, cur = [], [0]

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if path == "" and k == "output_contract":
                    s = raw.find('"output_contract"', cur[0])  # 契约说明不是事实，但游标要越过它
                    cur[0] = s if s >= 0 else cur[0]
                    walk_skip(v)
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, (str, int, float)) and not isinstance(o, bool):
            s = json.dumps(o, ensure_ascii=False)
            s = s[1:-1] if isinstance(o, str) else s
            if not s or len(s) > max_len:
                i = raw.find(s, cur[0]) if s else -1
                if i >= 0:
                    cur[0] = i + len(s)
                return
            i = raw.find(s, cur[0])
            if i >= 0:
                cur[0] = i + len(s)
                out.append({"json_path": path, "value": o, "file": rel, "file_char_start": i, "file_char_end": i + len(s),
                            "line": raw.count("\n", 0, i) + 1, "quote": s})

    def walk_skip(v):
        s = json.dumps(v, ensure_ascii=False)
        last = list(v.values())[-1] if isinstance(v, dict) and v else None
        if isinstance(last, str):
            i = raw.find(json.dumps(last, ensure_ascii=False)[1:-1], cur[0])
            if i >= 0:
                cur[0] = i + len(last)
        del s
    walk(case, "")
    return out


NON_MATERIAL = {"task.md", "rubric.md", "contract.md", "resources.yaml"}


def ingest(inp: Path, case_path: Path, out_dir: Path):
    mats, blocks = [], []
    skip = {case_path.resolve()}
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.resolve() in skip or out_dir.resolve() in p.resolve().parents:
            continue
        if p.name.startswith(".") or "workorder" in p.name.lower():
            continue
        if p.name.lower() in NON_MATERIAL or re.search(r"(output[-_]?contract|readme)", p.name, re.I):
            continue  # 题面/契约说明不是稿件材料
        rel = str(p.relative_to(inp))
        ext = p.suffix.lower()
        rec = {"file": rel, "bytes": p.stat().st_size, "sha256_16": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}
        try:
            if ext in TEXT_EXT:
                txt = p.read_text(encoding="utf-8", errors="replace")
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-B")
                rec.update(parser="text", text=txt)
            elif ext == ".pdf":
                pages = pdf_text_pages(p)
                b, base, parts = [], 0, []
                for i, pt in enumerate(pages, 1):
                    pb = blocks_from_text(pt, rel, page=i, id_prefix=f"{p.stem[:12]}-p{i}-B")
                    for x in pb:  # 偏移换算成“各页以 \f 连接后的全文”上的全局偏移
                        x["file_char_start"] += base
                    b += pb
                    parts.append(pt)
                    base += len(pt) + 1
                txt = "\f".join(parts)
                rec.update(parser=ENGINES["pdf_parse"][-1], pages=len(pages), text=txt, offsets_refer_to="extracted_text")
                if sum(len(x.strip()) for x in pages) < 50 * max(1, len(pages)):
                    WARNINGS.append(f"{rel}: little or no text layer (possible scanned PDF); OCR not attempted on PDF pages")
            elif ext == ".docx":
                txt = "\n\n".join(docx_paragraphs(p))
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-B")
                rec.update(parser=ENGINES["docx_parse"][-1], text=txt, offsets_refer_to="extracted_text")
            elif ext == ".json":
                b = json_blocks(json.loads(p.read_text(encoding="utf-8")), rel)
                rec.update(parser="json")
            elif ext in (".html", ".htm", ".xhtml", ".xml"):
                txt = html_text(p.read_text(encoding="utf-8", errors="replace"))
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-B")
                rec.update(parser="stdlib-html", text=txt, offsets_refer_to="extracted_text")
            elif ext in (".csv", ".tsv"):
                raw = p.read_text(encoding="utf-8", errors="replace")
                rows = list(csv.reader(io.StringIO(raw), delimiter="\t" if ext == ".tsv" else ","))
                txt = "\n".join("| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows if r)
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-T")
                for x in b:
                    x["kind"] = "table"
                rec.update(parser="stdlib-csv", text=txt, offsets_refer_to="extracted_text")
            elif ext == ".xlsx":
                txt = xlsx_text(p)
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-T")
                rec.update(parser="stdlib-zipfile-xlsx", text=txt, offsets_refer_to="extracted_text")
            elif ext == ".pptx":
                txt = pptx_text(p)
                b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-S")
                rec.update(parser="stdlib-zipfile-pptx", text=txt, offsets_refer_to="extracted_text")
            elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp"):
                b = []
                if shutil.which("tesseract"):
                    try:
                        r = subprocess.run(["tesseract", str(p), "-", "-l", "eng+chi_sim"], capture_output=True, timeout=90)
                        txt = r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
                        b = blocks_from_text(txt, rel, id_prefix=f"{p.stem[:12]}-O")
                        rec.update(parser="tesseract-ocr", text=txt, offsets_refer_to="ocr_text")
                    except Exception as e:
                        WARNINGS.append(f"OCR failed on {rel}: {e}")
                if not b:
                    rec.update(parser="image-no-ocr", note="image material recorded but no text extracted")
                    WARNINGS.append(f"image material {rel}: no OCR text (tesseract unavailable or empty)")
            else:
                rec.update(parser="unsupported", note="format not supported; skipped")
                WARNINGS.append(f"unsupported material format skipped: {rel}")
                b = []
        except Exception as e:
            rec.update(parser="error", note=f"{type(e).__name__}: {e}")
            WARNINGS.append(f"failed to parse {rel}: {e}")
            b = []
        rec["n_blocks"] = len(b)
        if rec.get("offsets_refer_to") and rec.get("text"):
            # 二进制/HTML 材料：把实际抽取的文本落盘，evidence 的 file_char_start/end 指向这份文本，可独立复核
            xp = out_dir / "work" / "extracted" / (rel.replace("/", "__") + ".txt")
            xp.parent.mkdir(parents=True, exist_ok=True)
            xp.write_text(rec["text"], encoding="utf-8")
            rec["extracted_text_file"] = str(xp.relative_to(out_dir))
            for x in b:
                x["extracted_text_file"] = rec["extracted_text_file"]
        mats.append(rec)
        blocks += b
    return mats, blocks


# ----------------------------------------------------------------------------- analysis
NUM_UNIT = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?(?:\s*[–\-]\s*\d+(?:\.\d+)?)?)\s*"
    r"(°C/min|°C|MPa/s|MPa|GPa|kN|kg/m³|kg/m3|m²/kg|mm|μm|µm|nm|cm|m|%|h|min|s|kV|days?|K|wt%|vol%)(?![A-Za-z])")
CITE = re.compile(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")
ABBR = re.compile(r"([A-Za-z][A-Za-z\- ]{2,80}?)\s*\(([A-Z][A-Za-z0-9\-/]{1,10})\)")
XREF = re.compile(r"\b(Table|Figure|Fig\.|Eq\.|Equation|Section)\s*([0-9]+(?:\.[0-9]+)?)([a-z])?")
STANDARD = re.compile(r"\b(ISO\s?\d+(?:-\d+)*|GB/T\s?\d+(?:-\d+)*|EN\s?\d+(?:-\d+)*|ASTM\s?[A-Z]\d+)")
SELF_CLAIM = re.compile(r"(has been verified|all properly referenced|are all cited|consistent across chapters|all abbreviations defined)", re.I)
REF_HEAD = re.compile(r"^(references|bibliography|参考文献|literature cited)$", re.I)
CONCL_HEAD = re.compile(r"(conclusion|abstract|summary|结论|摘要)", re.I)


def expand_cites(s: str):
    out = []
    for part in re.split(r"\s*,\s*", s):
        m = re.match(r"(\d+)\s*[–\-]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += list(range(a, b + 1)) if 0 < b - a < 50 else [a, b]
        elif part.strip().isdigit():
            out.append(int(part))
    return out


ABBR_DOT = re.compile(r"(?:\b(?:Eq|Eqs|Fig|Figs|al|e\.g|i\.e|etc|No|Ref|Refs|vs|approx|ca|Sect|Tab)|\b[A-Z])\.$")


def sentences(text: str):
    """句子切分；不在 Eq./Fig./et al./小数点 处断句。返回 (起点, 句子)。"""
    start, i, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in "。！？!?" or (c == "." and (i + 1 == n or text[i + 1] in " \n\t")):
            if c == "." and ABBR_DOT.search(text[start:i + 1]):
                i += 1
                continue
            j = i + 1
            while j < n and text[j] in ".!?。！？\"”)":
                j += 1
            seg = text[start:j]
            if seg.strip():
                yield start + (len(seg) - len(seg.lstrip())), seg.strip()
            start = j
            i = j
            continue
        i += 1
    seg = text[start:]
    if seg.strip():
        yield start + (len(seg) - len(seg.lstrip())), seg.strip()


def loc(block, start=None, end=None):
    d = {"file": block["file"], "block_id": block["id"], "chapter": block["chapter"]}
    if block.get("page"):
        d["page"] = block["page"]
    if block.get("line"):
        d["line"] = block["line"]
    if start is not None:
        d["char_start"], d["char_end"] = start, end
        if block.get("file_char_start") is not None:
            d["file_char_start"], d["file_char_end"] = block["file_char_start"] + start, block["file_char_start"] + end
        d["quote"] = block["text"][start:end]
    if block.get("json_path"):
        d["json_path"] = block["json_path"]
    if block.get("extracted_text_file"):
        d["offsets_refer_to"] = block["extracted_text_file"]
    return d


def is_ref_entry(b):
    return bool(re.match(r"^\[(\d+)\]\s+\S", b["text"])) or bool(REF_HEAD.match(b["chapter"] or ""))


def analyze(blocks):
    issues, terms, refs = [], {}, {}
    body = [b for b in blocks if b["kind"] != "heading"]
    # reference list
    for b in body:
        m = re.match(r"^\[(\d+)\]\s+(.+)", b["text"])
        if m and (REF_HEAD.match(b["chapter"] or "") or re.search(r"\(\d{4}\)|\d{4}[.;,]", m.group(2))):
            refs[int(m.group(1))] = {"n": int(m.group(1)), "entry": m.group(2), "locator": loc(b, 0, len(b["text"]))}
    text_blocks = [b for b in body if not (re.match(r"^\[\d+\]\s", b["text"]) and int(re.match(r"^\[(\d+)\]", b["text"]).group(1)) in refs)]
    # citations
    cited = {}
    for b in text_blocks:
        for m in CITE.finditer(b["text"]):
            for n in expand_cites(m.group(1)):
                cited.setdefault(n, []).append(loc(b, m.start(), m.end()))
    if refs or cited:
        for n, ls in sorted(cited.items()):
            if refs and n not in refs:
                issues.append({"type": "citation_missing_in_reference_list", "severity": "high", "key": f"[{n}]",
                               "detail": f"引用 [{n}] 在正文出现 {len(ls)} 次，但参考文献表中没有第 {n} 条（表中共 {len(refs)} 条）",
                               "locators": ls[:5], "needs_human": True})
        for n, r in sorted(refs.items()):
            if n not in cited:
                issues.append({"type": "reference_never_cited", "severity": "medium", "key": f"[{n}]",
                               "detail": f"参考文献 [{n}] 未在正文中被引用", "locators": [r["locator"]], "needs_human": True})
    # author-name vs reference entry (e.g. "Kalifa et al. [7]")
    for b in text_blocks:
        for m in re.finditer(r"([A-Z][a-z]+)\s+et al\.\s*\[(\d+)\]", b["text"]):
            n = int(m.group(2))
            if n in refs and m.group(1).lower() not in refs[n]["entry"].lower():
                issues.append({"type": "citation_author_mismatch", "severity": "high", "key": f"{m.group(1)}[{n}]",
                               "detail": f"正文 “{m.group(0)}” 与参考文献 [{n}] 的作者不一致：{refs[n]['entry'][:80]}",
                               "locators": [loc(b, m.start(), m.end()), refs[n]["locator"]], "needs_human": True})
    # abbreviations / terminology
    for b in text_blocks:
        for m in ABBR.finditer(b["text"]):
            ab = m.group(2)
            if not re.search(r"[A-Z]{2}", ab) and not re.fullmatch(r"[A-Z][a-z]?[A-Z]?", ab):
                continue
            words = m.group(1).split()
            k = max(1, min(len(words), len([c for c in ab if c.isupper()]) + 1))
            full = " ".join(words[-k:]).strip()
            terms.setdefault(ab, {"abbr": ab, "definitions": [], "uses": []})
            terms[ab]["definitions"].append({"expansion": full, **loc(b, m.start(2) - 1, m.end(2) + 1)})
    for ab, t in terms.items():
        pat = re.compile(r"(?<![\w-])" + re.escape(ab) + r"(?![\w-])")
        for b in text_blocks:
            for m in pat.finditer(b["text"]):
                t["uses"].append(loc(b, m.start(), m.end()))
        first_def = t["definitions"][0]
        first_use = t["uses"][0] if t["uses"] else None
        if first_use and (first_use["block_id"], first_use["char_start"]) != (first_def["block_id"], first_def["char_start"] + 1) \
                and blocks_index(blocks, first_use["block_id"]) < blocks_index(blocks, first_def["block_id"]):
            issues.append({"type": "abbreviation_used_before_definition", "severity": "medium", "key": ab,
                           "detail": f"缩写 {ab} 在 {first_use['block_id']} 先使用，定义在 {first_def['block_id']}",
                           "locators": [first_use, first_def], "needs_human": False})
        if len(t["definitions"]) > 1:
            exps = {d["expansion"].lower() for d in t["definitions"]}
            issues.append({"type": "abbreviation_redefined", "severity": "low" if len(exps) == 1 else "medium", "key": ab,
                           "detail": f"缩写 {ab} 被定义 {len(t['definitions'])} 次" + ("" if len(exps) == 1 else f"，展开不一致：{sorted(exps)}"),
                           "locators": [{k: v for k, v in d.items() if k != 'expansion'} for d in t["definitions"]], "needs_human": False})
    # spelling variants (fiber/fibre etc.)
    for a, bb in (("fiber", "fibre"), ("color", "colour"), ("modeling", "modelling"), ("behavior", "behaviour")):
        la = [loc(b, m.start(), m.end()) for b in text_blocks for m in re.finditer(a, b["text"], re.I)]
        lb = [loc(b, m.start(), m.end()) for b in blocks for m in re.finditer(bb, b["text"], re.I)]
        if la and lb:
            issues.append({"type": "terminology_spelling_variant", "severity": "low", "key": f"{a}/{bb}",
                           "detail": f"同一术语两种拼写并存：{a}×{len(la)}，{bb}×{len(lb)}（含标题/参考文献，后者原文不得改动）",
                           "locators": (la[:2] + lb[:2]), "needs_human": True})
    # cross-references: tables/figures/equations
    xrefs = {}
    captions = {}
    for b in body:
        cm = CAPTION.match(b["text"])
        if cm:
            kind = {"Fig.": "Figure", "表": "Table", "图": "Figure"}.get(cm.group(1), cm.group(1))
            captions[(kind, cm.group(2))] = loc(b, cm.start(), cm.end())
        for m in XREF.finditer(b["text"]):
            kind = {"Fig.": "Figure", "Equation": "Eq."}.get(m.group(1), m.group(1))
            if cm and m.start() == cm.start(1):
                continue
            xrefs.setdefault((kind, m.group(2)), []).append(loc(b, m.start(), m.end()))
    for kind in ("Table", "Figure"):
        nums = sorted({int(n) for (k, n) in list(xrefs) + list(captions) if k == kind and n.isdigit()})
        if nums:
            gaps = [n for n in range(1, max(nums) + 1) if n not in nums]
            if gaps:
                issues.append({"type": f"{kind.lower()}_numbering_gap", "severity": "medium", "key": kind,
                               "detail": f"{kind} 编号不连续：出现 {nums}，缺 {gaps}（未在正文出现）",
                               "locators": [xrefs.get((kind, str(n)), [captions.get((kind, str(n)))])[0] for n in nums if xrefs.get((kind, str(n))) or captions.get((kind, str(n)))][:4],
                               "needs_human": True})
            for n in nums:
                if (kind, str(n)) not in captions:
                    issues.append({"type": f"{kind.lower()}_caption_not_in_text", "severity": "low", "key": f"{kind} {n}",
                                   "detail": f"{kind} {n} 被引用但所给文本中没有其题注/本体（可能只在原 PDF 中），无法核对内容",
                                   "locators": xrefs.get((kind, str(n)), [])[:3], "needs_human": True})
    for (kind, n), ls in xrefs.items():
        if kind == "Eq.":
            defined = any(re.search(r"\(" + re.escape(n) + r"\)\s*$", b["text"]) for b in body) or \
                any(re.search(r"(proposed|given|expressed|defined)\s+(in|as)\s+Eq\.\s*" + re.escape(n), b["text"]) for b in body)
            if not defined:
                issues.append({"type": "equation_label_undefined", "severity": "medium", "key": f"Eq. {n}",
                               "detail": f"Eq. {n} 被引用，但材料中没有带该编号的公式定义",
                               "locators": ls[:3], "needs_human": True})
    # numeric facts + keyphrase conflicts
    facts = []
    for b in text_blocks:
        for m in NUM_UNIT.finditer(b["text"]):
            pre = b["text"][max(0, m.start() - 60):m.start()]
            words = re.findall(r"[A-Za-z][A-Za-z\-]+", pre)
            stop = {"of", "was", "were", "is", "at", "to", "from", "and", "the", "a", "an", "with", "approximately", "by", "about", "than", "for", "in", "exceeding"}
            unitish = {"mm", "cm", "nm", "mpa", "gpa", "kn", "min", "kg", "kv", "days", "day", "sf", "pp", "x"}
            kp = [w.lower() for w in words if w.lower() not in stop and w.lower() not in unitish and len(w) >= 3][-2:]
            if len(kp) < 2:
                kp = []
            facts.append({"value": m.group(1), "unit": m.group(2), "text": m.group(0), "keyphrase": " ".join(kp),
                          **loc(b, m.start(), m.end())})
    groups = {}
    for f in facts:
        if f["keyphrase"] and f["unit"] not in ("%",):
            groups.setdefault((f["keyphrase"], f["unit"]), []).append(f)
    for (kp, unit), fs in groups.items():
        vals = {f["value"] for f in fs}
        chs = {f["chapter"] for f in fs}
        if len(vals) > 1 and len(chs) > 1:
            issues.append({"type": "cross_chapter_value_divergence", "severity": "medium", "key": f"{kp} [{unit}]",
                           "detail": f"“{kp}” 在不同章节出现不同数值 {sorted(vals)} {unit}；可能是不同条件，也可能是冲突，需作者确认",
                           "locators": [{k: v for k, v in f.items() if k not in ('value', 'unit', 'text', 'keyphrase')} for f in fs][:6],
                           "needs_human": True})
    # table cells marked not reported
    for b in body:
        if b["kind"] != "table":
            continue
        rows = [r for r in b["text"].splitlines() if r.strip().startswith("|") and not re.fullmatch(r"[\s|:\-]+", r)]
        if len(rows) < 2:
            continue
        hdr = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        for r in rows[1:]:
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            for j, c in enumerate(cells):
                if c in ("—", "-", "–", "n.r.", "NR", "N/A", "n/a", "未报告"):
                    s = b["text"].find(r)
                    issues.append({"type": "table_value_not_reported", "severity": "medium", "key": f"{cells[0]}/{hdr[j] if j < len(hdr) else j}",
                                   "detail": f"表格 {b['id']} 行“{cells[0]}”列“{hdr[j] if j < len(hdr) else j}”为“{c}”（材料未报告）；正文若引用该值须核实，不得补造",
                                   "locators": [loc(b, s, s + len(r))], "needs_human": True})
    # conclusions/abstract numbers traceable to body?
    body_text = "\n".join(b["text"] for b in text_blocks if not CONCL_HEAD.search(b["chapter"] or ""))
    for f in facts:
        if CONCL_HEAD.search(f["chapter"] or "") and not re.search(
                r"(?<![\d.])" + re.escape(f["value"]) + r"\s*" + re.escape(f["unit"]), body_text):
            issues.append({"type": "conclusion_value_not_traceable", "severity": "high", "key": f["text"],
                           "detail": f"结论/摘要中的数值 {f['text']} 在正文其他章节找不到出处",
                           "locators": [{k: v for k, v in f.items() if k not in ('value', 'unit', 'text', 'keyphrase')}], "needs_human": True})
    # self-claims of consistency vs findings
    hard = [i for i in issues if i["type"] in ("citation_missing_in_reference_list", "figure_numbering_gap",
                                                "table_numbering_gap", "equation_label_undefined", "abbreviation_used_before_definition")]
    for b in text_blocks:
        for m in SELF_CLAIM.finditer(b["text"]):
            if hard:
                issues.append({"type": "self_claim_contradicted", "severity": "high", "key": b["id"],
                               "detail": f"该段自称 “{m.group(0)}”，但本次检查发现 {len(hard)} 个编号/引用/缩写问题（见 {', '.join(sorted({h['type'] for h in hard}))}）",
                               "locators": [loc(b, m.start(), m.end())], "needs_human": True})
                break
    for i, it in enumerate(issues, 1):
        it["issue_id"] = f"I{i:03d}"
    return {"issues": issues, "terms": terms, "refs": refs, "cited": cited, "xrefs": xrefs, "captions": captions, "facts": facts}


_IDX: dict = {}


def blocks_index(blocks, bid):
    if not _IDX or len(_IDX) != len(blocks):
        _IDX.clear()
        _IDX.update({b["id"]: i for i, b in enumerate(blocks)})
    return _IDX.get(bid, 10 ** 9)


# ----------------------------------------------------------------------------- annotation drafting
def is_cjk(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text)) > 0.2 * max(1, len(text))


def draft_annotation(tb, blocks, an, ea_no):
    """为目标块起草一条有来源的编辑注释：条件/限制/可追溯说明；全部引语逐字取自原文。"""
    text = tb["text"]
    quotes, related, flags = [], [], []
    # 1) 目标块里承载数值/引用/标准/编号的句子
    sents = [(s0, s) for s0, s in sentences(text) if NUM_UNIT.search(s) or CITE.search(s) or STANDARD.search(s) or XREF.search(s)]
    sents = sents or list(sentences(text))[:1]
    for s0, s in sents[:3]:
        quotes.append(loc(tb, s0, s0 + len(s)))
    # 2) 同一实体（标准、图表公式编号、数值+单位、缩写）在其他块中的出现
    ents = set(m.group(1) for m in STANDARD.finditer(text))
    ents |= {f"{m.group(1)} {m.group(2)}" for m in XREF.finditer(text)}
    vals = [(m.group(1), m.group(2)) for m in NUM_UNIT.finditer(text)]
    others = [b for b in blocks if b["id"] != tb["id"] and b["kind"] != "heading" and not re.match(r"^\[\d+\]\s", b["text"])]
    seen = set()
    for e in sorted(ents):
        for b in others:
            i = b["text"].find(e)
            if i >= 0 and (b["id"], e) not in seen:
                seen.add((b["id"], e))
                ss = next(((s0, s) for s0, s in sentences(b["text"]) if s0 <= i < s0 + len(s)), (0, b["text"]))
                related.append({"entity": e, **loc(b, ss[0], ss[0] + len(ss[1]))})
                break
    for v, u in vals:
        for b in others:
            m = re.search(r"(?<![\d.])" + re.escape(v) + r"\s*" + re.escape(u), b["text"])
            if m and (b["id"], v + u) not in seen:
                seen.add((b["id"], v + u))
                ss = next(((s0, s) for s0, s in sentences(b["text"]) if s0 <= m.start() < s0 + len(s)), (0, b["text"]))
                related.append({"entity": f"{v} {u}", **loc(b, ss[0], ss[0] + len(ss[1]))})
                break
    related = related[:5]
    # 3) 与本块相关的检查结果
    for it in an["issues"]:
        if any(l and l.get("block_id") == tb["id"] for l in it["locators"]):
            flags.append(it)
    cites = sorted({n for m in CITE.finditer(text) for n in expand_cites(m.group(1))})
    missing = [n for n in cites if an["refs"] and n not in an["refs"]]
    present = [n for n in cites if n in an["refs"]]
    zh = is_cjk(text)
    q0 = quotes[0]["quote"]
    parts = []
    if zh:
        parts.append(f"【编辑注释 EA-{ea_no}｜由 weekly-skill 起草，非原研究内容】原文“{q0}”")
        if vals:
            parts.append("所述条件为：" + "、".join(f"{v} {u}" for v, u in vals[:6]) + "（数值与单位按原文保留，未作换算）。")
        for r in related[:3]:
            parts.append(f"同一要素“{r['entity']}”在 {r['block_id']}（{r['chapter']}）表述为“{r['quote']}”，读者应结合该处条件理解本段。")
        if present:
            parts.append("本段引用 " + "、".join(f"[{n}]" for n in present) + " 可在参考文献表中找到对应条目。")
        if missing:
            parts.append("但引用 " + "、".join(f"[{n}]" for n in missing) + " 在所给参考文献表中不存在，相应论断目前无法从材料中核实。")
        for it in flags[:3]:
            if it["type"] not in ("citation_missing_in_reference_list",):
                parts.append(f"检查提示（{it['issue_id']}）：{it['detail']}。")
        parts.append("不确定性：以上仅依据所给材料；未新增实验或外部事实，是否需要改动正文由作者确认。")
    else:
        parts.append(f"[Editorial annotation EA-{ea_no} — drafted by weekly-skill; not part of the original research] The passage “{q0}”")
        if vals:
            parts.append("is conditional on the values stated here: " + ", ".join(f"{v} {u}" for v, u in vals[:6]) + " (kept verbatim, no unit conversion).")
        else:
            parts.append("is kept verbatim.")
        for r in related[:3]:
            parts.append(f"The same element “{r['entity']}” appears in {r['block_id']} ({r['chapter']}): “{r['quote']}”; this paragraph should be read together with that condition.")
        if present:
            parts.append("Citation(s) " + ", ".join(f"[{n}]" for n in present) + " resolve to entries in the reference list.")
        if missing:
            parts.append("Citation(s) " + ", ".join(f"[{n}]" for n in missing) + " have no entry in the supplied reference list, so the supporting claim cannot be verified from the material.")
        for it in flags[:3]:
            if it["type"] not in ("citation_missing_in_reference_list",):
                parts.append(f"Check note ({it['issue_id']}): {it['detail']}.")
        parts.append("Uncertainty: based only on the supplied material; no new experiments or external facts were added; whether the original text should change is left to the author.")
    return {
        "target_id": tb["id"], "annotation_id": f"EA-{ea_no}", "text": " ".join(parts),
        "source_quotes": quotes, "related_evidence": related, "issues_touching_target": [i["issue_id"] for i in flags],
        "missing_citations": missing, "language": "zh" if zh else "en", "origin": "auto_draft",
    }


# ----------------------------------------------------------------------------- revision engine (versioned, reversible)
class InvalidOperation(Exception):
    pass


class Manuscript:
    """块级稿件状态。原文块只读；注释作为独立块插入目标块之后。"""

    def __init__(self, blocks):
        self.blocks = [dict(b) for b in blocks]
        self.annotations = {}  # target_id -> annotation text
        self.versions = []

    def state_hash(self):
        return sha(json.dumps([[b["id"], b["text"]] for b in self.blocks] + sorted(self.annotations.items()), ensure_ascii=False))

    def apply(self, op):
        ids = {b["id"] for b in self.blocks}
        if op["op"] == "add_annotation":
            if op["target_id"] not in ids:
                raise InvalidOperation(f"target block {op['target_id']} does not exist")
            if not op.get("text", "").strip():
                raise InvalidOperation("empty annotation text")
            if op["target_id"] in self.annotations:
                raise InvalidOperation(f"target {op['target_id']} already annotated (duplicate)")
            self.annotations[op["target_id"]] = op["text"]
        elif op["op"] in ("replace_original_text", "delete_block"):
            raise InvalidOperation(f"{op['op']} on original source block {op.get('target_id')} is forbidden by source-fidelity rule")
        else:
            raise InvalidOperation(f"unknown operation {op['op']}")

    def render_md(self, ann_prefix="> "):
        out = []
        for b in self.blocks:
            tag = f"[{b['id']}] " if b.get("has_native_id") and b["kind"] != "heading" else ""
            out.append(tag + b["text"])
            if b["id"] in self.annotations:
                out.append(ann_prefix + self.annotations[b["id"]])
        return "\n\n".join(out) + "\n"


def run_revision(blocks, annotations, out_dir):
    ms = Manuscript(blocks)
    v0_md, v0_hash = ms.render_md(), ms.state_hash()
    log, proposals = [], []
    for a in annotations:
        ok = all(q["quote"] and q["quote"] in next(b["text"] for b in blocks if b["id"] == a["target_id"]) for q in a["source_quotes"])
        proposals.append({"proposal_id": f"PR-{len(proposals)+1}", "op": "add_annotation", "target_id": a["target_id"],
                          "text": a["text"], "decision": "accept" if ok and a["text"].strip() else "reject",
                          "reason": "all quoted source spans verified verbatim in target block; original text untouched" if ok
                          else "quoted span not found verbatim in target block"})
        # 同时提出一个“直接改原句”的对照提案 —— 按 source-fidelity 规则拒绝
        proposals.append({"proposal_id": f"PR-{len(proposals)+1}", "op": "replace_original_text", "target_id": a["target_id"],
                          "text": "(machine rewrite of the original paragraph)", "decision": "reject",
                          "reason": "workorder requires preserving original paragraph text; rewriting would silently alter source facts"})
    for p in proposals:
        before = ms.state_hash()
        if p["decision"] == "accept":
            try:
                ms.apply(p)
                log.append({"action": "accept_and_apply", "proposal_id": p["proposal_id"], "target_id": p["target_id"],
                            "reason": p["reason"], "state_before": before, "state_after": ms.state_hash()})
            except InvalidOperation as e:
                p["decision"] = "reject"
                log.append({"action": "apply_failed", "proposal_id": p["proposal_id"], "target_id": p["target_id"],
                            "reason": str(e), "state_before": before, "state_after": ms.state_hash()})
        else:
            log.append({"action": "reject", "proposal_id": p["proposal_id"], "target_id": p["target_id"],
                        "reason": p["reason"], "state_before": before, "state_after": ms.state_hash()})
    v1_md, v1_hash, v1_ann = ms.render_md(), ms.state_hash(), dict(ms.annotations)
    diff = "".join(difflib.unified_diff(v0_md.splitlines(True), v1_md.splitlines(True), "v0_original.md", "v1_revised.md"))
    # rollback + reapply (real, on the state object)
    ms.annotations = {}
    rb_hash = ms.state_hash()
    log.append({"action": "rollback_to_v0", "reason": "demonstrate reversible revision", "state_after": rb_hash,
                "matches_v0": rb_hash == v0_hash})
    for p in proposals:
        if p["decision"] == "accept":
            ms.apply(p)
    ra_hash = ms.state_hash()
    log.append({"action": "reapply_accepted", "reason": "re-apply accepted proposals after rollback", "state_after": ra_hash,
                "matches_v1": ra_hash == v1_hash})
    # controlled invalid operations (deliberate fault injection)
    some = blocks[0]["id"] if blocks else "none"
    tgt = annotations[0]["target_id"] if annotations else some
    for op in ({"op": "add_annotation", "target_id": "__NONEXISTENT_BLOCK__", "text": "x"},
               {"op": "add_annotation", "target_id": tgt, "text": "  "},
               {"op": "add_annotation", "target_id": tgt, "text": "duplicate"},
               {"op": "replace_original_text", "target_id": tgt, "text": "rewrite"}):
        before = ms.state_hash()
        try:
            ms.apply(op)
            res = "UNEXPECTEDLY_APPLIED"
        except InvalidOperation as e:
            res = f"InvalidOperation: {e}"
        log.append({"action": "invalid_operation_test", "deliberate_fault_injection": True, "operation": op["op"],
                    "target_id": op["target_id"], "reason": res, "state_before": before, "state_after": ms.state_hash(),
                    "state_unchanged": before == ms.state_hash()})
    (out_dir / "versions").mkdir(exist_ok=True)
    (out_dir / "versions" / "v0_original.md").write_text(v0_md, encoding="utf-8")
    (out_dir / "versions" / "v1_revised.md").write_text(v1_md, encoding="utf-8")
    (out_dir / "versions" / "v0_to_v1.diff").write_text(diff, encoding="utf-8")
    ms.annotations = v1_ann
    return ms, proposals, log, {"v0": v0_hash, "v1": v1_hash, "rollback": rb_hash, "reapply": ra_hash}


# ----------------------------------------------------------------------------- deliverables & rendering
def write_docx(ms: Manuscript, path: Path, title: str):
    docx = opt_import("docx")
    if docx:
        try:
            from docx.shared import RGBColor, Pt
            d = docx.Document()
            d.add_heading(title, level=0)
            for b in ms.blocks:
                if b["kind"] == "heading":
                    d.add_heading(re.sub(r"^#+\s*", "", b["text"]), level=1)
                elif b["kind"] == "table":
                    rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in b["text"].splitlines()
                            if r.strip() and not re.fullmatch(r"[\s|:\-]+", r)]
                    if rows:
                        t = d.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                        t.style = "Table Grid"
                        for i, r in enumerate(rows):
                            for j, c in enumerate(r):
                                t.cell(i, j).text = c
                else:
                    p = d.add_paragraph()
                    if b.get("has_native_id"):
                        rid = p.add_run(f"[{b['id']}] ")
                        rid.font.size = Pt(8)
                        rid.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
                    p.add_run(b["text"])
                if b["id"] in ms.annotations:
                    p = d.add_paragraph()
                    r = p.add_run(ms.annotations[b["id"]])
                    r.italic = True
                    r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x99)
            d.save(str(path))
            ENGINES["docx_write"] = "python-docx"
            return
        except Exception as e:
            WARNINGS.append(f"python-docx write failed, using stdlib OOXML: {e}")

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    paras = [f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t xml:space="preserve">{esc(title)}</w:t></w:r></w:p>']
    for b in ms.blocks:
        if b["kind"] == "heading":
            htxt = esc(re.sub(r"^#+\s*", "", b["text"]))
            paras.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t xml:space="preserve">' + htxt + '</w:t></w:r></w:p>')
        else:
            for line in b["text"].splitlines():
                tag = f"[{b['id']}] " if b.get("has_native_id") and line is b["text"].splitlines()[0] else ""
                paras.append(f'<w:p><w:r><w:t xml:space="preserve">{esc(tag + line)}</w:t></w:r></w:p>')
        if b["id"] in ms.annotations:
            paras.append(f'<w:p><w:r><w:rPr><w:i/><w:color w:val="1F4E99"/></w:rPr><w:t xml:space="preserve">{esc(ms.annotations[b["id"]])}</w:t></w:r></w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(paras) + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    ENGINES["docx_write"] = "stdlib-zipfile-ooxml(degraded)"


# 5x7 点阵字体（纯标准库 PNG 兜底渲染用；小写按大写绘制，非 ASCII 绘制为方框——降级但是真实文本栅格化）
_GLYPHS = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    ".": "00000 00000 00000 00000 00000 01100 01100", ",": "00000 00000 00000 00000 01100 00100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000", ";": "00000 01100 01100 00000 01100 00100 01000",
    "-": "00000 00000 00000 11111 00000 00000 00000", "_": "00000 00000 00000 00000 00000 00000 11111",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "[": "01110 01000 01000 01000 01000 01000 01110", "]": "01110 00010 00010 00010 00010 00010 01110",
    "/": "00001 00010 00010 00100 01000 01000 10000", "=": "00000 00000 11111 00000 11111 00000 00000",
    ">": "01000 00100 00010 00001 00010 00100 01000", "<": "00010 00100 01000 10000 01000 00100 00010",
    "#": "01010 01010 11111 01010 11111 01010 01010", "?": "01110 10001 00001 00010 00100 00000 00100",
    "!": "00100 00100 00100 00100 00100 00000 00100", "'": "00100 00100 01000 00000 00000 00000 00000",
    '"': "01010 01010 00000 00000 00000 00000 00000", "%": "11000 11001 00010 00100 01000 10011 00011",
    "+": "00000 00100 00100 11111 00100 00100 00000", "*": "00000 10101 01110 11111 01110 10101 00000",
    "&": "01100 10010 10100 01000 10101 10010 01101", "|": "00100 00100 00100 00100 00100 00100 00100",
    " ": "00000 00000 00000 00000 00000 00000 00000", "\x00": "11111 10001 10001 10001 10001 10001 11111",
}


def stdlib_png(lines, path: Path, scale=2, cols=100):
    """把文本行栅格化为灰度 PNG（zlib+struct，无第三方库）。"""
    import struct
    cw, chh, margin = 6, 10, 8
    w = (cols * cw + 2 * margin) * scale
    h = (len(lines) * chh + 2 * margin) * scale
    px = bytearray(b"\xff" * (w * h))
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line[:cols]):
            g = _GLYPHS.get(ch.upper()) or (_GLYPHS["\x00"] if ord(ch) > 127 else _GLYPHS["?"])
            for ry, row in enumerate(g.split()):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        x0, y0 = (margin + ci * cw + rx) * scale, (margin + li * chh + ry) * scale
                        for dy in range(scale):
                            o = (y0 + dy) * w + x0
                            px[o:o + scale] = b"\x00" * scale
    raw = b"".join(b"\x00" + bytes(px[y * w:(y + 1) * w]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def find_soffice():
    if FORCE_STDLIB:
        return None
    for c in ("soffice", "libreoffice"):
        if shutil.which(c):
            return shutil.which(c)
    for c in ("/Applications/LibreOffice.app/Contents/MacOS/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"):
        if os.path.exists(c):
            return c
    return None


def render_previews(docx_path: Path, md_text: str, prev_dir: Path, max_pages=4):
    prev_dir.mkdir(exist_ok=True)
    files, pdf = [], None
    so = find_soffice()
    if so:
        try:
            prof = tempfile.mkdtemp(prefix="lo-")
            r = subprocess.run([so, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf",
                                "--outdir", str(prev_dir), str(docx_path)], capture_output=True, timeout=180)
            cand = prev_dir / (docx_path.stem + ".pdf")
            if cand.exists() and cand.stat().st_size > 0:
                pdf = cand
                ENGINES["pdf_render"] = "libreoffice-soffice"
            else:
                WARNINGS.append(f"soffice produced no PDF (rc={r.returncode}): {r.stderr.decode(errors='replace')[:200]}")
        except Exception as e:
            WARNINGS.append(f"soffice failed: {e}")
    if pdf:
        files.append(pdf)
        if shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-png", "-r", "70", "-f", "1", "-l", str(max_pages), str(pdf), str(prev_dir / "page")],
                           capture_output=True, timeout=120)
            pngs = sorted(prev_dir.glob("page*.png"))
            if pngs:
                ENGINES["png_render"] = "pdftoppm"
                return files + pngs
        fitz = opt_import("fitz")
        if fitz:
            try:
                doc = fitz.open(str(pdf))
                for i, pg in enumerate(doc):
                    if i >= max_pages:
                        break
                    o = prev_dir / f"page-{i+1}.png"
                    pg.get_pixmap(dpi=70).save(str(o))
                    files.append(o)
                ENGINES["png_render"] = "pymupdf"
                return files
            except Exception as e:
                WARNINGS.append(f"fitz png render failed: {e}")
        if shutil.which("sips"):
            o = prev_dir / "page-1.png"
            subprocess.run(["sips", "-s", "format", "png", str(pdf), "--out", str(o)], capture_output=True, timeout=60)
            if o.exists() and o.stat().st_size > 0:
                ENGINES["png_render"] = "macOS-sips(first page only)"
                return files + [o]
    # fallback: matplotlib typesets the actual manuscript text into PDF/PNG pages
    plt = opt_import("matplotlib.pyplot")
    if plt:
        try:
            import textwrap
            from matplotlib.backends.backend_pdf import PdfPages
            lines = []
            for para in md_text.split("\n\n"):
                lines += textwrap.wrap(para, 110) + [""]
            per = 60
            pdfp = prev_dir / (docx_path.stem + ".pdf")
            with PdfPages(str(pdfp)) as pp:
                for k in range(0, min(len(lines), per * max_pages), per):
                    fig = plt.figure(figsize=(8.27, 11.69))
                    for j, ln in enumerate(lines[k:k + per]):
                        fig.text(0.05, 0.97 - j * 0.0158, ln, fontsize=6.5, family="DejaVu Sans",
                                 color="#1F4E99" if ln.startswith(">") else "black")
                    pp.savefig(fig)
                    png = prev_dir / f"page-{k // per + 1}.png"
                    fig.savefig(str(png), dpi=70)
                    files.append(png)
                    plt.close(fig)
            files.insert(0, pdfp)
            ENGINES["pdf_render"] = ENGINES["png_render"] = "matplotlib-text-typesetting(degraded)"
            return files
        except Exception as e:
            WARNINGS.append(f"matplotlib preview failed: {e}")
    # last resort: hand-written PDF (ASCII-safe) – still real text rendering
    txt = md_text.encode("ascii", "replace").decode()[:6000]
    import textwrap
    lines = [l for para in txt.split("\n\n") for l in textwrap.wrap(para, 95) + [""]][:70]
    content = "BT /F1 8 Tf 40 800 Td 11 TL " + " ".join("(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    buf, offs = io.BytesIO(), []
    buf.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(buf.tell())
        buf.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    x = buf.tell()
    buf.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
              + f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode())
    pdfp = prev_dir / (docx_path.stem + ".pdf")
    pdfp.write_bytes(buf.getvalue())
    ENGINES["pdf_render"] = "stdlib-handwritten-pdf(degraded: Helvetica, non-ASCII shown as ?)"
    res = [pdfp]
    try:
        pngp = prev_dir / "page-1.png"
        stdlib_png(lines, pngp)
        ENGINES["png_render"] = "stdlib-zlib-png-5x7-bitmap(degraded: uppercase only, non-ASCII as boxes)"
        res.append(pngp)
    except Exception as e:
        WARNINGS.append(f"stdlib PNG render failed: {e}")
    return res


def intake_report_blocks(case, receipt, mats, targets, missing_t, cfacts, wo):
    """无稿件时的状态报告内容：全部来自 case.json 原文与本次实际扫描结果。"""
    blks = []

    def add(kind, text):
        blks.append({"id": f"R{len(blks)+1:03d}", "has_native_id": False, "kind": kind, "chapter": "", "text": text, "file": "(report)"})
    add("heading", "# 1. Case and source (verbatim from case file)")
    for f in cfacts:
        if f["json_path"] in ("case_id", "material_version", "case_input_version") or f["json_path"].startswith("source."):
            add("paragraph", f"{f['json_path']}: {f['quote']}  (case file line {f['line']}, chars {f['file_char_start']}-{f['file_char_end']})")
    add("heading", "# 2. Material inventory (what the run actually found)")
    if mats:
        for m in mats:
            add("paragraph", f"{m['file']} — {m['bytes']} bytes, parser={m.get('parser')}, blocks={m.get('n_blocks', 0)}"
                + (f", note: {m['note']}" if m.get("note") else ""))
    else:
        add("paragraph", "No manuscript/source material file besides the case file (and task/contract text) was found in the input directory.")
    add("heading", "# 3. Workorder targets")
    if wo.get("id"):
        add("paragraph", f"Workorder: {wo.get('id')}")
    for t in targets:
        add("paragraph", f"{t}: " + ("NOT FOUND in any supplied material — no annotation written (nothing invented)." if t in missing_t else "found"))
    if not targets:
        add("paragraph", "The case declares no target ids.")
    add("heading", "# 4. Operation requirements and what was executed")
    for req in (wo.get("operation_requirements") or []):
        done = "import" in req.lower() or "receipt" in req.lower()
        add("paragraph", f"{req} → " + ("DONE: work/import_receipt.json written." if done else
                                         "NOT EXECUTED: requires the manuscript blocks, which were not supplied."))
    add("heading", "# 5. Next step for a human / agent")
    add("paragraph", "Place the manuscript (md/txt/docx/pdf/html/json, paragraphs tagged with the target ids, e.g. "
        + (f"[{targets[0]}]" if targets else "[ID]") + ") into the input directory and re-run the same command; "
        "all annotation, proposal, versioning and invalid-operation steps then run automatically.")
    return blks


# ----------------------------------------------------------------------------- result assembly
KNOWN = {"case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"}


def fill_contract_field(key, desc, ctx):
    k = key.lower()
    table = [
        (("deliverable", "draft", "manuscript", "artifact", "output_file"), "deliverable"),
        (("fact", "preserv"), "preserved_facts"),
        (("evidence", "source_map", "provenance", "trace"), "evidence_map"),
        (("change", "revision", "log", "diff"), "change_log"),
        (("term", "glossary"), "terminology"),
        (("reference", "citation", "bibliograph"), "references"),
        (("uncertain", "confirm", "human", "open_issue", "unresolved"), "human_confirmation"),
        (("consisten", "check", "issue"), "consistency_issues"),
    ]
    for keys, name in table:
        if any(x in k for x in keys):
            return ctx[name]
    return None


def main():
    ap = argparse.ArgumentParser(description="weekly-skill long-document revision pipeline")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    # SKILL.md 的入口命令带 `--work`；接受但不强制，保持命令行与文档一致
    # （否则 argparse 以退出码 2 结束，平台拿不到 result.json）。
    ap.add_argument("--work", default="", help="scratch dir (optional, CLI compatibility)")

    ap.add_argument("--annotations", default=None, help="optional JSON {target_id: text} to replace auto drafts")
    ap.add_argument("--max-targets", type=int, default=40)
    a = ap.parse_args()
    t0 = time.time()
    inp, out = Path(a.input), Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = pipeline(inp, out, a, t0)
    except Exception as e:  # never crash without a result.json
        tb = traceback.format_exc()
        case = {}
        cp = find_case(inp) if inp.exists() else None
        if cp:
            try:
                case = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
        enum = status_enum(case)
        st = next((e for e in (enum or []) if e.lower() in ("error", "failed", "input_error", "run_failed")), "error")
        result = {"case_id": case.get("case_id", "not_reported"), "status": st,
                  "notes": f"pipeline crashed: {type(e).__name__}: {e}", "traceback": tb[-3000:], "engines": ENGINES, "warnings": WARNINGS}
        for kk in (case.get("output_contract") or {}):
            result.setdefault(kk, "not_reported")
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(tb, file=sys.stderr)
        sys.exit(1)
    print(json.dumps({k: result[k] for k in ("case_id", "status")}, ensure_ascii=False))
    print("summary:", json.dumps(result["run_summary"], ensure_ascii=False))


def pipeline(inp: Path, out: Path, a, t0):
    work = out / "work"
    work.mkdir(exist_ok=True)
    cp = find_case(inp)
    if not cp:
        raise FileNotFoundError(f"no case.json (or json with case_id) under {inp}")
    case = json.loads(cp.read_text(encoding="utf-8"))
    merge_workorder(case, inp)
    contract = case.get("output_contract") or {}
    wo = case.get("organizer_edit_workorder") or {}
    targets = list(wo.get("target_ids") or case.get("target_ids") or [])
    receipt = {"case_file": str(cp), "case_id": case.get("case_id"), "material_version": case.get("material_version"),
               "workorder_id": wo.get("id"), "target_ids": targets, "contract_fields": list(contract),
               "received_at_unix": round(t0, 3)}
    set_dynamic_id_pattern(targets)
    mats, blocks = ingest(inp, cp, out)
    receipt["materials"] = [{k: v for k, v in m.items() if k != "text"} for m in mats]
    (work / "import_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    # choose the manuscript = material holding most target ids, else largest text material
    by_file = {}
    for b in blocks:
        by_file.setdefault(b["file"], []).append(b)
    tset = set(targets)
    score = {f: (sum(1 for b in bs if b["id"] in tset), sum(len(b["text"]) for b in bs)) for f, bs in by_file.items()}
    main_file = max(score, key=lambda f: score[f]) if score else None
    # 章节分文件提供时：把所有含目标块或含原生块号的非 PDF 文件按文件名顺序合并为一份稿件
    multi = sorted(f for f, bs in by_file.items() if not f.lower().endswith(".pdf")
                   and (score[f][0] > 0 or sum(b["has_native_id"] for b in bs) >= 3))
    if len(multi) > 1:
        seen_ids, mblocks = set(), []
        for f in multi:
            for b in by_file[f]:
                if b["id"] not in seen_ids:
                    seen_ids.add(b["id"])
                    mblocks.append(b)
        main_file = " + ".join(multi)
    else:
        mblocks = by_file.get(main_file, [])
    # 其他材料（如原论文 PDF）用作原文对照来源
    source_pages = []
    for f, bs in by_file.items():
        if f not in multi and f != main_file:
            for b in bs:
                source_pages.append((f, b.get("page"), re.sub(r"\s+", " ", b["text"])))
    an = analyze(mblocks) if mblocks else {"issues": [], "terms": {}, "refs": {}, "cited": {}, "xrefs": {}, "captions": {}, "facts": []}
    present = [t for t in targets if any(b["id"] == t for b in mblocks)]
    missing_t = [t for t in targets if t not in present]
    # 兜底：稿件没有任何原生块号、而目标号形如 XXX-P057 时，按“第 57 个正文段落”定位，并标为需人工确认
    positional = {}
    if missing_t and mblocks and not any(b["has_native_id"] for b in mblocks):
        paras = [b for b in mblocks if b["kind"] in ("paragraph", "caption", "table")]
        for t in list(missing_t):
            m = re.search(r"P(\d+)$", t, re.I)
            if m and 1 <= int(m.group(1)) <= len(paras):
                b = paras[int(m.group(1)) - 1]
                positional[t] = b["id"]
                b["auto_id"], b["id"], b["has_native_id"] = b["id"], t, True
                present.append(t)
                missing_t.remove(t)
                WARNINGS.append(f"target {t} has no explicit marker in the material; mapped by paragraph ordinal to "
                                f"{b['file']} line {b.get('line')} (needs human confirmation)")
    receipt["target_resolution"] = {t: ("explicit_block_id" if t in present and t not in positional else
                                        f"paragraph_ordinal_fallback (was {positional[t]})" if t in positional else "not_found")
                                    for t in targets}
    receipt["manuscript_file"], receipt["manuscript_blocks"] = main_file, len(mblocks)
    (work / "import_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    auto_targets = False
    if not targets and mblocks:  # no workorder targets: annotate the paragraphs with most issues
        cnt = {}
        for it in an["issues"]:
            for l in it["locators"]:
                if l and l.get("block_id"):
                    cnt[l["block_id"]] = cnt.get(l["block_id"], 0) + 1
        present = [bid for bid, _ in sorted(cnt.items(), key=lambda x: -x[1])][:2]
        auto_targets = True
    if len(present) > a.max_targets:  # 超长截断：未处理的 target 计入缺失并报告，不静默丢弃
        WARNINGS.append(f"targets truncated at --max-targets={a.max_targets}: {present[a.max_targets:]}")
        missing_t += present[a.max_targets:]
        present = present[: a.max_targets]
    # annotations (auto draft, optionally replaced by agent-refined text)
    override = {}
    if a.annotations:
        override = json.loads(Path(a.annotations).read_text(encoding="utf-8"))
    annotations = []
    for i, t in enumerate(present, 1):
        tb = next(b for b in mblocks if b["id"] == t)
        ann = draft_annotation(tb, mblocks, an, i)
        if t in override:
            ov = override[t] if isinstance(override[t], dict) else {"text": override[t]}
            bad = [q for q in ov.get("quotes", []) if q not in tb["text"]]
            if bad:
                WARNINGS.append(f"override for {t} rejected: quotes not verbatim in source: {bad}")
            else:
                ann["text"], ann["origin"] = ov["text"], "agent_refined (quotes verified)"
                for q in ov.get("quotes", []):
                    s = tb["text"].find(q)
                    ann["source_quotes"].append(loc(tb, s, s + len(q)))
        # 对照原始来源（如原论文 PDF）：目标引语是否在原文中逐字出现
        for q in ann["source_quotes"]:
            key = re.sub(r"\s+", " ", q["quote"])[:80]
            hit = next(((f, pg) for f, pg, t in source_pages if key and key in t), None)
            q["found_in_original_source"] = ({"file": hit[0], "page": hit[1]} if hit else
                                             ("no_other_source_supplied" if not source_pages else "not_found_verbatim"))
            if source_pages and not hit:
                WARNINGS.append(f"{t}: quote not found verbatim in other supplied sources (may be hyphenation/ligature differences)")
        annotations.append(ann)
    ms, proposals, rlog, hashes = run_revision(mblocks, annotations, out) if mblocks else (Manuscript([]), [], [], {})
    # deliverables
    cid = str(case.get("case_id", "case"))
    safe = re.sub(r"[^\w.\-]+", "_", cid)
    try:
        case_rel = str(cp.resolve().relative_to(inp.resolve()))
    except ValueError:
        case_rel = cp.name
    cfacts = case_facts(cp, case, case_rel)
    if mblocks:
        md_path, docx_path = out / f"{safe}_revised_manuscript.md", out / f"{safe}_revised_manuscript.docx"
        md_text = ms.render_md()
        # Prepend metadata preamble with case-level facts so they're verifiable in revised manuscript
        meta_lines = ["# Document metadata", ""]
        for f in cfacts:
            jp = f["json_path"]
            if jp.startswith("source.") or jp in ("material_version", "case_input_version") or jp.startswith("organizer_edit_workorder.target_ids") \
                    or jp == "organizer_edit_workorder.id":
                meta_lines.append(f"- {jp} = {f['quote']}")
        meta_lines.append("")
        md_text = "\n".join(meta_lines) + "\n" + md_text
        title = ((case.get("source") or {}).get("title") or cid) + " — revised working chapters (annotations marked)"
        write_docx(ms, docx_path, title)
    else:
        # 没有可编辑稿件：交付一份真实的“接收/状态报告”（不是稿件，不含任何编造注释），照常导出 docx 与 PDF/PNG 预览
        md_path, docx_path = out / f"{safe}_intake_status_report.md", out / f"{safe}_intake_status_report.docx"
        rep_ms = Manuscript(intake_report_blocks(case, receipt, mats, targets, missing_t, cfacts, wo))
        md_text = rep_ms.render_md()
        write_docx(rep_ms, docx_path, f"{cid} — intake & status report (no manuscript material supplied; NOT a revised manuscript)")
        WARNINGS.append("no manuscript material found in the input directory: annotations, proposals and versioning were not executed")
    md_path.write_text(md_text, encoding="utf-8")
    previews = render_previews(docx_path, md_text, out / "preview")
    # verify original text preserved in revised version
    preserved_ok = all(b["text"] in md_text for b in mblocks)
    # preserved facts: numeric facts in targets + related blocks
    focus = set(present) | {r["block_id"] for x in annotations for r in x["related_evidence"]}
    pfacts = []
    for f in an["facts"]:
        if f["block_id"] in focus:
            pfacts.append({"fact": f["text"], "keyphrase": f["keyphrase"], "source": {k: f[k] for k in ("file", "block_id", "chapter", "char_start", "char_end", "quote") if k in f}
                           | ({"file_char_start": f["file_char_start"], "file_char_end": f["file_char_end"]} if "file_char_start" in f else {}),
                           "changed": False, "verified_in_revised": f["quote"] in md_text})
    # evidence map
    emap = []
    for x in annotations:
        for q in x["source_quotes"]:
            emap.append({"claim": f"{x['annotation_id']} at {x['target_id']} quotes original text", "claim_type": "target_quote",
                         "locator": {k: v for k, v in q.items() if k != "quote"}, "quote": q["quote"], "annotation_id": x["annotation_id"]})
        for r in x["related_evidence"]:
            emap.append({"claim": f"{x['annotation_id']}: element “{r['entity']}” also stated in {r['block_id']}", "claim_type": "cross_chapter_link",
                         "locator": {k: v for k, v in r.items() if k not in ('quote', 'entity')}, "quote": r["quote"], "annotation_id": x["annotation_id"]})
    for it in an["issues"]:
        for l in it["locators"][:2]:
            if l and l.get("quote"):
                emap.append({"claim": f"{it['issue_id']} {it['type']}: {it['detail']}", "claim_type": "consistency_finding",
                             "locator": {k: v for k, v in l.items() if k != "quote"}, "quote": l["quote"], "issue_id": it["issue_id"]})
                break
    for n, r in sorted(an["refs"].items()):
        emap.append({"claim": f"reference [{n}] entry", "claim_type": "reference_entry", "locator": {k: v for k, v in r["locator"].items() if k != "quote"},
                     "quote": r["locator"]["quote"]})
    # case-level facts (source title/DOI/version/workorder/targets) — verbatim spans of the case file
    for f in cfacts:
        jp = f["json_path"]
        if jp.startswith("source.") or jp in ("material_version", "case_input_version") or jp.startswith("organizer_edit_workorder.target_ids") \
                or jp == "organizer_edit_workorder.id":
            lc = {k: f[k] for k in ("file", "json_path", "line", "file_char_start", "file_char_end")}
            pfacts.append({"fact": f"{jp} = {f['quote']}", "keyphrase": f["quote"], "source": dict(lc, quote=f["quote"]),
                           "changed": False, "verified_in_revised": f["quote"] in md_text, "fact_type": "case_metadata"})
            claim = (f"workorder target {f['quote']} " + ("not found in any supplied material" if f["quote"] in missing_t else "located in manuscript")
                     if "target_ids" in jp else f"case declares {jp}")
            emap.append({"claim": claim, "claim_type": "case_metadata", "locator": lc, "quote": f["quote"]})
    # terminology & references tables
    with open(out / "terminology.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["abbr", "expansion(first definition)", "defined_in", "n_definitions", "n_uses", "first_use"])
        for ab, t in sorted(an["terms"].items()):
            d0 = t["definitions"][0]
            w.writerow([ab, d0["expansion"], f"{d0['block_id']}:{d0['char_start']}", len(t["definitions"]), len(t["uses"]),
                        f"{t['uses'][0]['block_id']}" if t["uses"] else ""])
    with open(out / "references_table.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["n", "in_reference_list", "times_cited", "cited_in_blocks", "entry"])
        for n in sorted(set(an["refs"]) | set(an["cited"])):
            cs = an["cited"].get(n, [])
            w.writerow([n, n in an["refs"], len(cs), " ".join(sorted({c["block_id"] for c in cs})), (an["refs"].get(n) or {}).get("entry", "")])
    human = [{"item": it["issue_id"], "type": it["type"], "detail": it["detail"],
              "where": sorted({l["block_id"] for l in it["locators"] if l})} for it in an["issues"] if it.get("needs_human")]
    for t in missing_t:
        human.append({"item": f"missing-target-{t}", "type": "target_not_found", "detail": f"工单目标 {t} 在材料中找不到，未作注释", "where": [t]})
    # consistency report (human readable)
    rep = [f"# Consistency report — {cid}", "", f"Manuscript analysed: `{main_file}` ({len(mblocks)} blocks).", "",
           "| id | type | severity | detail | where |", "|---|---|---|---|---|"]
    for it in an["issues"]:
        where = ", ".join(sorted({l["block_id"] for l in it["locators"] if l}))
        rep.append(f"| {it['issue_id']} | {it['type']} | {it['severity']} | {it['detail'].replace('|', '/')} | {where} |")
    rep += ["", "## Annotations", ""] + [f"- **{x['annotation_id']} → {x['target_id']}** ({x['origin']}): {x['text']}" for x in annotations]
    (out / "consistency_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    (out / "proposals.json").write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")
    if not mblocks:  # 无稿件时这些表必然为空：不留下空壳文件
        for p in ("consistency_report.md", "terminology.csv", "references_table.csv", "proposals.json"):
            (out / p).unlink()
    # change log
    change_log = []
    for e in rlog:
        change_log.append(e)
    for h in human:
        change_log.append({"action": "flag_for_human_confirmation", "target_id": ",".join(h["where"]), "reason": h["detail"], "item": h["item"]})
    change_log.insert(0, {"action": "import_receipt", "reason": f"case file {case_rel} read; {len(mats)} other material file(s) scanned; "
                          f"{len(mblocks)} manuscript block(s); targets requested={targets}", "file": "work/import_receipt.json"})
    if not mblocks:
        change_log.append({"action": "no_revision_performed", "target_id": ",".join(targets),
                           "reason": "no manuscript material in the input directory, so no annotation/proposal/version/rollback/invalid-op step "
                                     "could run on real blocks; nothing was invented. Deliverable is an intake/status report.",
                           "human_check": True, "unresolved": True})
    all_done = bool(present) and not missing_t and all(p["decision"] == "accept" for p in proposals if p["op"] == "add_annotation")
    enum = status_enum(case)
    status = pick_status(enum, all_done and not auto_targets)
    cands = [md_path, docx_path, work / "import_receipt.json"]
    if mblocks:
        cands += [out / "consistency_report.md", out / "terminology.csv", out / "references_table.csv", out / "proposals.json",
                  out / "versions/v0_original.md", out / "versions/v1_revised.md", out / "versions/v0_to_v1.diff"]
    artifacts = [str(p.relative_to(out)) for p in cands + list(previews) if p.exists() and p.stat().st_size > 0]
    artifacts += [m["extracted_text_file"] for m in mats if m.get("extracted_text_file")]
    ctx = {
        "deliverable": ({"editable_manuscript_md": md_path.name, "editable_manuscript_docx": docx_path.name,
                         "annotations": [{k: x[k] for k in ("annotation_id", "target_id", "text", "origin", "missing_citations")} for x in annotations],
                         "original_text_preserved_verbatim": preserved_ok, "previews": [str(p.relative_to(out)) for p in previews],
                         "note": "Annotations are editorial additions inserted after each target block; original paragraphs untouched."}
                        if mblocks else
                        {"kind": "intake_status_report", "editable_report_md": md_path.name, "editable_report_docx": docx_path.name,
                         "previews": [str(p.relative_to(out)) for p in previews], "annotations": [],
                         "revised_manuscript": "not_reported",
                         "not_reported_reason": "no manuscript material was supplied in the input directory; targets "
                                                + ", ".join(missing_t) + " could not be located, so no annotation was written",
                         "note": "This docx is a status report, not a revised manuscript. Re-run with the manuscript placed in the input directory."}),
        "preserved_facts": pfacts, "evidence_map": emap, "change_log": change_log,
        "terminology": [{"abbr": ab, "expansion": t["definitions"][0]["expansion"], "defined_in": t["definitions"][0]["block_id"], "uses": len(t["uses"])} for ab, t in sorted(an["terms"].items())],
        "references": [{"n": n, "in_list": n in an["refs"], "times_cited": len(an["cited"].get(n, []))} for n in sorted(set(an["refs"]) | set(an["cited"]))],
        "human_confirmation": human, "consistency_issues": an["issues"],
    }
    result = {"case_id": case.get("case_id", "not_reported")}
    notes = []
    for key, desc in contract.items():
        if key == "case_id":
            continue
        v = ctx.get(key) if key in ctx else fill_contract_field(key, desc, ctx)
        if v is None or v == [] or v == {}:
            result[key] = "not_reported"
            notes.append(f"contract field `{key}` ({desc}) could not be filled from the material → not_reported")
        else:
            result[key] = v
    result["status"] = status
    result["status_reason"] = ("all workorder targets annotated with verified quotes; open items listed for human confirmation" if status == pick_status(enum, True)
                               else f"partial: targets_missing={missing_t}, auto_selected_targets={auto_targets}")
    result["consistency_issues"] = an["issues"]
    result["human_confirmation"] = human
    result["version_record"] = ({"state_hashes": hashes, "rollback_matches_v0": hashes.get("rollback") == hashes.get("v0"),
                                 "reapply_matches_v1": hashes.get("reapply") == hashes.get("v1"), "diff_file": "versions/v0_to_v1.diff"}
                                if mblocks else {"executed": False, "reason": "no manuscript blocks to version"})
    result["target_resolution"] = receipt.get("target_resolution", {})
    result["sources"] = receipt["materials"]
    result["artifacts"] = artifacts
    result["engines"] = ENGINES
    result["warnings"] = WARNINGS
    result["uncertainty"] = ("Automatic checks are pattern-based (citations, numbering, abbreviations, numeric keyphrases); a divergence flag is a prompt for "
                             "author review, not proof of an error. Annotations restate and link supplied material only; scientific judgement remains with the author.")
    notes.append(f"preview/render engines: {json.dumps(ENGINES, ensure_ascii=False)}")
    result["notes"] = " ".join(notes)
    result["run_summary"] = {"skill_version": SKILL_VERSION, "manuscript_file": main_file, "blocks": len(mblocks),
                             "targets_requested": len(targets), "targets_annotated": len(annotations), "targets_missing": missing_t,
                             "issues": len(an["issues"]), "evidence_entries": len(emap), "preserved_facts": len(pfacts),
                             "human_confirmation_items": len(human), "elapsed_seconds": round(time.time() - t0, 2)}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    _reexec_under_weekly_tools()
    main()
