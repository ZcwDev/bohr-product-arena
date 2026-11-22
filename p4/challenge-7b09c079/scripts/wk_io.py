#!/usr/bin/env python3
"""Input readers + locator model (stdlib only; optional libs are used when present).

Every piece of text the Skill quotes comes from a *block* produced here.  A block
carries a locator that a reviewer (and selfcheck.py) can resolve back to the
original file:

    {"file": "<path relative to input dir>", "kind": "text|docx|pdf|json",
     "line_start"/"line_end" (text), "para" (docx, 1-based <w:p> index in body),
     "page" + "block" (pdf, 1-based), "json_path" (json),
     "char_start"/"char_end" (offsets of the quote inside the block text),
     "extractor": "fitz|pdftotext|stdlib-flate|..."}

All readers raise ReadError (never a bare exception) so the caller can degrade.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".tex", ".rst"}
DOC_SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf", ".json"}


class ReadError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def norm_cmp(s: str) -> str:
    """Normalisation used for quote verification (whitespace, dashes, quotes, soft hyphen)."""
    s = (s or "").replace("\u00ad", "").replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"[\u2018\u2019\u201a\u2032]", "'", s)
    s = re.sub(r"[\u201c\u201d\u201e\u2033]", '"', s)
    s = re.sub(r"-\s*\n\s*", "", s)  # de-hyphenate line breaks
    return re.sub(r"\s+", " ", s).strip().lower()


# ----------------------------------------------------------------- text files
def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ReadError("undecodable_text", str(path))


def text_blocks(text: str) -> list[dict]:
    """Blank-line separated blocks with 1-based line spans."""
    blocks, buf, start = [], [], 1
    lines = text.splitlines()
    for i, line in enumerate(lines + [""], 1):
        if line.strip() == "":
            if buf:
                blocks.append({"text": "\n".join(buf), "line_start": start, "line_end": i - 1})
                buf = []
        else:
            if not buf:
                start = i
            buf.append(line)
    for b in blocks:
        t = b["text"].lstrip()
        b["is_heading"] = bool(re.match(r"#{1,6}\s", t)) or bool(re.match(r"\\(sub)*section\*?\{", t))
        b["is_table"] = t.startswith("|") and "|" in t[1:]
        b["is_equation"] = bool(re.match(r"(\$\$|\\begin\{equation|\\\[)", t)) or (
            b["text"].startswith("    ") and "=" in t and len(t) < 200)
        b["is_reference_entry"] = False
    return blocks


# ----------------------------------------------------------------- docx
def _docx_open(path: Path) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        head = path.read_bytes()[:8]
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise ReadError("encrypted_or_legacy_office",
                            f"{path} is an OLE container (password-protected OOXML or legacy .doc)")
        raise ReadError("corrupt_docx", f"{path} is not a valid ZIP/OOXML package")
    if "word/document.xml" not in zf.namelist():
        raise ReadError("corrupt_docx", f"{path} has no word/document.xml")
    return zf


def docx_paragraphs(path: Path) -> list[dict]:
    """Body-level paragraphs (tables flattened to one block per table)."""
    zf = _docx_open(path)
    try:
        root = ET.fromstring(zf.read("word/document.xml"))
    except ET.ParseError as exc:
        raise ReadError("corrupt_docx", f"document.xml not well-formed: {exc}")
    body = root.find(W + "body")
    out, idx = [], 0
    for el in list(body if body is not None else []):
        if el.tag == W + "p":
            idx += 1
            # visible text only (w:t); deleted runs (w:delText) are not part of the current text
            txt = "".join(t.text or "" for t in el.iter(W + "t"))
            style = el.find(f"{W}pPr/{W}pStyle")
            sname = style.get(W + "val") if style is not None else ""
            out.append({"text": txt, "para": idx, "style": sname,
                        "is_heading": bool(re.match(r"(?i)heading|title|标题", sname or "")),
                        "is_table": False, "is_equation": el.find(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath") is not None,
                        "is_reference_entry": False})
        elif el.tag == W + "tbl":
            idx += 1
            rows = []
            for tr in el.iter(W + "tr"):
                rows.append(" | ".join("".join(t.text or "" for t in tc.iter(W + "t")) for tc in tr.iter(W + "tc")))
            out.append({"text": "\n".join(rows), "para": idx, "style": "table", "is_heading": False,
                        "is_table": True, "is_equation": False, "is_reference_entry": False})
    return out


def docx_footnotes_headers(path: Path) -> dict:
    zf = _docx_open(path)
    res = {}
    for name in zf.namelist():
        if re.match(r"word/(footnotes|endnotes|header\d*|footer\d*|comments)\.xml$", name):
            try:
                root = ET.fromstring(zf.read(name))
                res[name] = norm_ws(" ".join(t.text or "" for t in root.iter(W + "t")))
            except ET.ParseError:
                res[name] = "unparseable"
    return res


# ----------------------------------------------------------------- pdf
def _pdf_fitz(path: Path):
    import fitz  # type: ignore
    doc = fitz.open(str(path))
    if doc.needs_pass:
        raise ReadError("encrypted_pdf", f"{path} requires a password")
    pages = []
    for page in doc:
        blocks = []
        for b in page.get_text("blocks"):
            if len(b) >= 7 and b[6] != 0:
                continue  # image block
            t = b[4].strip()
            if t:
                blocks.append({"text": t, "bbox": [round(float(x), 1) for x in b[:4]]})
        pages.append(blocks)
    return pages, "fitz"


def _pdf_pdftotext(path: Path):
    exe = shutil.which("pdftotext")
    if not exe:
        raise ReadError("no_pdftotext", "pdftotext not on PATH")
    cp = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=120)
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace")
        if "ncrypt" in err or "assword" in err:
            raise ReadError("encrypted_pdf", err.strip())
        raise ReadError("pdf_extract_failed", err.strip())
    pages = []
    for ptxt in cp.stdout.decode("utf-8", "replace").split("\f"):
        blocks = [{"text": b.strip()} for b in re.split(r"\n\s*\n", ptxt) if b.strip()]
        pages.append(blocks)
    if pages and not pages[-1]:
        pages.pop()
    return pages, "pdftotext"


def _pdf_unescape(s: bytes) -> str:
    out, i = bytearray(), 0
    esc = {b"n"[0]: 10, b"r"[0]: 13, b"t"[0]: 9, b"b"[0]: 8, b"f"[0]: 12}
    while i < len(s):
        c = s[i]
        if c == 92 and i + 1 < len(s):
            n = s[i + 1]
            if n in esc:
                out.append(esc[n]); i += 2
            elif 48 <= n <= 55:
                m = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
                out.append(int(m.group(0), 8) & 255); i += 1 + len(m.group(0))
            elif n in (10, 13):
                i += 2
            else:
                out.append(n); i += 2
        else:
            out.append(c); i += 1
    return out.decode("latin-1")


def _pdf_stdlib(path: Path):
    """Minimal fallback: FlateDecode content streams, Tj/TJ/'/\" operators."""
    data = path.read_bytes()
    if b"/Encrypt" in data:
        raise ReadError("encrypted_pdf", f"{path} has an /Encrypt dictionary")
    objs = {}
    for m in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", data, re.S):
        objs[int(m.group(1))] = m.group(2)

    def stream_of(body: bytes) -> bytes:
        m = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S)
        if not m:
            return b""
        raw = m.group(1)
        if b"FlateDecode" in body[:m.start()]:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                try:
                    return zlib.decompressobj().decompress(raw)
                except zlib.error:
                    return b""
        return raw

    def text_ops(content: bytes) -> str:
        parts = []
        for bt in re.findall(rb"BT(.*?)ET", content, re.S):
            line = []
            for tok in re.finditer(rb"\[(.*?)\]\s*TJ|\((.*?)(?<!\\)\)\s*(?:Tj|'|\")|(T\*|Td|TD)", bt, re.S):
                if tok.group(1) is not None:
                    for sm in re.finditer(rb"\((.*?)(?<!\\)\)|(-?\d+\.?\d*)", tok.group(1), re.S):
                        if sm.group(1) is not None:
                            line.append(_pdf_unescape(sm.group(1)))
                        elif sm.group(2) and float(sm.group(2)) < -200:
                            line.append(" ")
                elif tok.group(2) is not None:
                    line.append(_pdf_unescape(tok.group(2)))
                else:
                    line.append("\n")
            parts.append("".join(line))
        return "\n".join(parts)

    pages = []
    for num in sorted(objs):
        body = objs[num]
        if not re.search(rb"/Type\s*/Page(?!s)", body):
            continue
        cm = re.search(rb"/Contents\s*(\[(.*?)\]|(\d+)\s+0\s+R)", body, re.S)
        refs = []
        if cm:
            refs = [int(x) for x in re.findall(rb"(\d+)\s+0\s+R", cm.group(1))]
        txt = "\n".join(text_ops(stream_of(objs.get(r, b""))) for r in refs)
        pages.append([{"text": b.strip()} for b in re.split(r"\n\s*\n", txt) if b.strip()] or
                     ([{"text": txt.strip()}] if txt.strip() else []))
    return pages, "stdlib-flate"


def pdf_pages(path: Path, prefer: str | None = None):
    """Returns (pages, extractor). pages = list[list[{'text',...}]]."""
    names = {"fitz": _pdf_fitz, "pdftotext": _pdf_pdftotext, "stdlib-flate": _pdf_stdlib}
    order = [names[prefer]] if prefer in names else [_pdf_fitz, _pdf_pdftotext, _pdf_stdlib]
    errors = []
    for fn in order:
        try:
            pages, name = fn(path)
            if sum(len(b["text"]) for p in pages for b in p) > 0:
                return pages, name
            errors.append(f"{fn.__name__}: empty text")
        except ReadError as exc:
            if exc.code == "encrypted_pdf":
                raise
            errors.append(f"{fn.__name__}: {exc.detail}")
        except ImportError:
            errors.append(f"{fn.__name__}: library not installed")
        except Exception as exc:  # noqa: BLE001 - extractor bugs must not kill the run
            errors.append(f"{fn.__name__}: {exc!r}")
    raise ReadError("no_text_layer", "; ".join(errors))


# ----------------------------------------------------------------- json
TEXT_KEYS = ("text", "content", "body", "paragraph", "markdown", "md")
ID_KEYS = ("id", "block_id", "para_id", "paragraph_id", "pid", "anchor", "target_id")


def json_blocks(obj, path="$") -> list[dict]:
    """Find dicts that look like {id, text} paragraphs anywhere in a JSON tree."""
    out = []
    if isinstance(obj, dict):
        tid = next((obj[k] for k in ID_KEYS if isinstance(obj.get(k), (str, int))), None)
        tkey = next((k for k in TEXT_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None)
        if tkey and tid is not None:
            out.append({"id": str(tid), "text": obj[tkey], "json_path": f"{path}.{tkey}",
                        "section": obj.get("section") or obj.get("heading") or obj.get("title") or ""})
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out.extend(json_blocks(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(json_blocks(v, f"{path}[{i}]"))
    return out


def json_get(obj, jpath: str):
    cur = obj
    for tok in re.findall(r"\.([^.\[]+)|\[(\d+)\]", jpath[1:]):
        cur = cur[tok[0]] if tok[0] else cur[int(tok[1])]
    return cur


# ----------------------------------------------------------------- locators
def resolve_locator(input_dir: Path, loc: dict, cache: dict | None = None) -> str:
    """Return the text region a locator points to (raises ReadError)."""
    cache = {} if cache is None else cache
    f = input_dir / loc["file"]
    if not f.is_file():
        raise ReadError("locator_file_missing", str(f))
    kind = loc.get("kind")
    if kind == "text":
        lines = read_text_file(f).splitlines()
        return "\n".join(lines[int(loc["line_start"]) - 1:int(loc["line_end"])])
    if kind == "docx":
        key = ("docx", str(f))
        if key not in cache:
            cache[key] = docx_paragraphs(f)
        for p in cache[key]:
            if p["para"] == int(loc["para"]):
                return p["text"]
        raise ReadError("locator_para_missing", f"{f} para {loc['para']}")
    if kind == "pdf":
        key = ("pdf", str(f), loc.get("extractor"))
        if key not in cache:
            cache[key] = pdf_pages(f, loc.get("extractor"))[0]
        pages = cache[key]
        page = pages[int(loc["page"]) - 1]
        if loc.get("block"):
            return page[int(loc["block"]) - 1]["text"]
        return "\n".join(b["text"] for b in page)
    if kind == "json":
        return str(json_get(json.loads(read_text_file(f)), loc["json_path"]))
    raise ReadError("locator_kind_unknown", str(kind))


def locator_str(loc: dict) -> str:
    k = loc.get("kind")
    if k == "text":
        s = f"{loc['file']}:L{loc['line_start']}-L{loc['line_end']}"
    elif k == "docx":
        s = f"{loc['file']}#para={loc['para']}"
    elif k == "pdf":
        s = f"{loc['file']}#page={loc['page']}" + (f"&block={loc['block']}" if loc.get("block") else "")
    elif k == "json":
        s = f"{loc['file']}#{loc['json_path']}"
    else:
        s = str(loc.get("file"))
    if "char_start" in loc:
        s += f"[c{loc['char_start']}:{loc['char_end']}]"
    return s


def quote_in(region: str, quote: str) -> bool:
    return norm_cmp(quote) in norm_cmp(region)


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) char spans, robust to abbreviations like 'Fig.' / 'et al.'."""
    spans, start = [], 0
    for m in re.finditer(r"(?<=[.!?。！？;；])\s+(?=[A-Z0-9(\[“\"\u4e00-\u9fff])|(?<=[。！？])", text):
        prev = text[max(0, m.start() - 6):m.start()]
        if re.search(r"(?:\b(?:Fig|Figs|Eq|Eqs|Ref|Refs|al|vs|e\.g|i\.e|approx|No|ca|resp)\.)$", prev):
            continue
        seg = text[start:m.start()]
        if seg.strip():
            spans.append((start, m.start(), seg))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text), text[start:]))
    return spans
