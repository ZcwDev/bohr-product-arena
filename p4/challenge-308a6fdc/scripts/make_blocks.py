#!/usr/bin/env python3
"""Turn a case's raw material into blocks that carry a resolvable locator.

`pdf_text.py` already does this for PDFs (locator `p<page>b<block>`). Writing
cases also ship .md/.txt/.json/.csv/.docx material, and for those the only
honest locator is a line range in the actual file. This produces the same
`blocks.json` shape so `evidence.py` and `contract.py --blocks` can resolve
every locator back to real bytes.

Locator syntax:
  text   <basename>:L12-L19        (1-based, inclusive)
  docx   <basename>:p3             (paragraph 3 of word/document.xml)
  pdf    p4b7                      (delegated to pdf_text.py)

CLI:
  python3 make_blocks.py IN -o blocks.json [--max-chars 1200] [--stride 1]
  python3 make_blocks.py IN --resolve "in.md:L3-L5"     # verify one locator
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile

TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".csv", ".tsv", ".json", ".jsonl",
            ".yaml", ".yml", ".tex", ".bib", ".xml", ".html", ".py", ".log"}


def _split_paragraphs(lines: list[str]) -> list[tuple[int, int]]:
    """Return (start, end) 1-based line ranges, blank-line delimited."""
    spans, start = [], None
    for i, line in enumerate(lines, start=1):
        blank = not line.strip()
        if not blank and start is None:
            start = i
        if blank and start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(lines)))
    return spans


def _chunk(span: tuple[int, int], lines: list[str], max_chars: int) -> list[tuple[int, int]]:
    start, end = span
    out, cur_start, size = [], start, 0
    for i in range(start, end + 1):
        size += len(lines[i - 1]) + 1
        if size > max_chars and i > cur_start:
            out.append((cur_start, i - 1))
            cur_start, size = i, len(lines[i - 1]) + 1
    out.append((cur_start, end))
    return out


def index_text(path: str, max_chars: int = 1200, stride: int = 1) -> dict:
    base = os.path.basename(path)
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    blocks = []
    for span in _split_paragraphs(lines):
        for s, e in _chunk(span, lines, max_chars):
            text = "\n".join(lines[s - 1:e]).strip()
            if not text:
                continue
            blocks.append({"locator": f"{base}:L{s}-L{e}", "source": base,
                           "line_start": s, "line_end": e, "type": "body", "text": text})
            if stride > 1:
                for off in range(1, stride):
                    s2 = s + off
                    if s2 > e:
                        break
                    blocks.append({"locator": f"{base}:L{s2}-L{e}", "source": base,
                                   "line_start": s2, "line_end": e, "type": "body",
                                   "text": "\n".join(lines[s2 - 1:e]).strip()})
    return {"source": base, "engine": "lines", "page_count": 0,
            "warnings": [], "blocks": blocks,
            "line_count": len(lines)}


def index_docx(path: str, max_chars: int = 1200) -> dict:
    base = os.path.basename(path)
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    paras = re.findall(r"<w:p\b.*?</w:p>", xml, re.S)
    blocks = []
    for i, para in enumerate(paras, start=1):
        texts = re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", para, re.S)
        text = "".join(texts)
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"')).strip()
        if not text:
            continue
        style = re.search(r'w:pStyle w:val="([^"]+)"', para)
        kind = "heading" if style and "eading" in style.group(1) else "body"
        blocks.append({"locator": f"{base}:p{i}", "source": base, "para": i,
                       "type": kind, "text": text})
    return {"source": base, "engine": "docx-xml", "page_count": 0,
            "warnings": [], "blocks": blocks}


def index_pdf(path: str) -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import pdf_text  # type: ignore
    data = pdf_text.extract(path)
    data["source"] = os.path.basename(path)
    return data


def build(path: str, max_chars: int = 1200, stride: int = 1) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return index_pdf(path)
    if ext == ".docx":
        return index_docx(path, max_chars)
    if ext in TEXT_EXT or ext == "":
        return index_text(path, max_chars, stride)
    try:
        return index_text(path, max_chars, stride)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"cannot index {path}: {exc}; convert to text/PDF first, "
                           f"or report the format as unsupported rather than guessing content") from exc


LINE_LOC = re.compile(r"^(?P<file>[^:]+):L(?P<a>\d+)(?:-L(?P<b>\d+))?$")
PARA_LOC = re.compile(r"^(?P<file>[^:]+):p(?P<p>\d+)$")
PDF_LOC = re.compile(r"^p(?P<page>\d+)b(?P<block>\d+)$")


def resolve(locator: str, root: str = ".") -> dict:
    """Say whether a locator points at bytes that really exist. Never guesses."""
    locator = (locator or "").strip()
    if not locator:
        return {"locator": locator, "resolved": False, "reason": "empty locator"}
    if locator.lower() in ("not_reported", "unable", "conflict"):
        return {"locator": locator, "resolved": True, "reason": "explicit absence marker"}
    m = LINE_LOC.match(locator)
    if m:
        path = m.group("file") if os.path.isabs(m.group("file")) else os.path.join(root, m.group("file"))
        if not os.path.isfile(path):
            return {"locator": locator, "resolved": False, "reason": f"no such file: {path}"}
        n = len(open(path, encoding="utf-8", errors="replace").read().splitlines())
        a = int(m.group("a"))
        b = int(m.group("b") or m.group("a"))
        ok = 1 <= a <= b <= n
        return {"locator": locator, "resolved": ok, "file": path, "line_count": n,
                "reason": "ok" if ok else f"line range {a}-{b} outside 1-{n}"}
    m = PARA_LOC.match(locator)
    if m:
        path = m.group("file") if os.path.isabs(m.group("file")) else os.path.join(root, m.group("file"))
        if not os.path.isfile(path):
            return {"locator": locator, "resolved": False, "reason": f"no such file: {path}"}
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
        n = len(re.findall(r"<w:p\b", xml))
        p = int(m.group("p"))
        return {"locator": locator, "resolved": 1 <= p <= n, "paragraph_count": n,
                "reason": "ok" if 1 <= p <= n else f"paragraph {p} outside 1-{n}"}
    if PDF_LOC.match(locator):
        return {"locator": locator, "resolved": True,
                "reason": "PDF locator: resolve against blocks.json with evidence.py"}
    return {"locator": locator, "resolved": False,
            "reason": "unrecognised locator syntax; use <file>:Lx-Ly, <file>:pN or pXbY"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Index material into locator-carrying blocks")
    ap.add_argument("input")
    ap.add_argument("-o", "--out")
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--stride", type=int, default=1,
                    help="emit a block for each additional start line (quote matching)", dest="stride")
    ap.add_argument("--resolve", help="verify a single locator and exit")
    ap.add_argument("--root", default=".", help="base directory for relative locators")
    a = ap.parse_args()

    if a.resolve:
        res = resolve(a.resolve, a.root)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["resolved"] else 1

    data = build(a.input, a.max_chars, a.stride)
    if not data.get("blocks"):
        print(f"no text extracted from {a.input}: report this as unreadable instead of writing from memory",
              file=sys.stderr)
        return 1
    if a.out:
        json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({"out": a.out, "engine": data["engine"], "blocks": len(data["blocks"]),
                          "locator_example": data["blocks"][0]["locator"]}, ensure_ascii=False))
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
