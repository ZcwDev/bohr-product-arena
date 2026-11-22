#!/usr/bin/env python3
"""Reading-client entry point: one command surface over a material directory.

Subcommands (all standard library only):

  index  MATERIAL_DIR_OR_FILE -o docindex.json   build the reusable middle representation
  search docindex.json "phrase"                  keyword/phrase lookup -> locators + context
  show   docindex.json <locator>                 print the block text, verbatim
  cite   docindex.json <locator>                 one pasteable citation line, not_reported for gaps
  digest docindex.json                            per-section skeleton + locators, no conclusions

The index is a single JSON file so every later subcommand (and evidence.py) can reuse it
without touching the source again.

Locator convention
  PDF          p<page>b<block>            (from pdf_text.py; block order within a page)
  text/code    <file>:<line>              (line where the paragraph starts)
  multi-doc    <source_id>:<locator>      (only when the index holds more than one document)

`show` prints the stored verbatim slice. For text files that slice is the original bytes
of the file (line breaks included). For PDFs it is the text the extractor recovered;
a PDF has no separable "original text" beyond that, so the engine is reported with it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".log",
            ".json", ".yaml", ".yml", ".py", ".sh", ".c", ".h", ".cpp", ".java",
            ".js", ".ts", ".go", ".rs", ".sql", ".html", ".xml", ".ini", ".cfg",
            ".conf", ".toml"}
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
             ".idea", ".mypy_cache", ".pytest_cache", ".DS_Store"}
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_TEXT_LINES = 40000

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
             "−": "-", " ": " ", "‘": "'", "’": "'", "“": '"', "”": '"'}
MD_HEAD = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
CITE_LINE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+\S")


def norm(text: str) -> str:
    """Whitespace/ligature normalisation for matching only; stored text is never rewritten."""
    out = "".join(LIGATURES.get(ch, ch) for ch in text)
    out = unicodedata.normalize("NFKC", out)
    out = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", out)
    return re.sub(r"\s+", " ", out).strip()


def _read_text(path: str) -> tuple[str, list[str]]:
    warnings = []
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) > MAX_TEXT_BYTES:
        warnings.append(f"file is {len(raw)} bytes, indexed the first {MAX_TEXT_BYTES}")
        raw = raw[:MAX_TEXT_BYTES]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        n = text.count("�")
        warnings.append(f"not valid UTF-8: {n} replacement characters (U+FFFD) in the index")
    return text, warnings


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Blank-line separated paragraphs; an ATX heading is always its own block.

    Markdown often puts a heading directly above body text with no blank line
    (`# Title\\nbody...`), so headings are split out before grouping.
    """
    out: list[tuple[int, str]] = []
    lines = text.splitlines(keepends=True)
    buf: list[str] = []
    start = 1

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append((start, "".join(buf)))
            buf = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if MD_HEAD.match(stripped):
            flush()
            out.append((i, line))
            continue
        if not buf:
            start = i
        buf.append(line)
    flush()
    return out


def index_text_file(path: str, source_id: str) -> dict:
    text, warnings = _read_text(path)
    lines = text.splitlines()
    if len(lines) > MAX_TEXT_LINES:
        warnings.append(f"{len(lines)} lines, indexed the first {MAX_TEXT_LINES}")
    blocks = []
    for start, raw in _paragraphs(text):
        if start > MAX_TEXT_LINES:
            break
        stripped = raw.strip()
        if not stripped:
            continue
        head = MD_HEAD.match(stripped.splitlines()[0]) if stripped.startswith("#") else None
        blocks.append({
            "locator": f"{os.path.basename(path)}:{start}",
            "line": start,
            "page": None,
            "type": "heading" if head else "body",
            "level": len(head.group(1)) if head else None,
            "section": head.group(2) if head else None,
            "text": stripped,
            "raw": raw,
        })
    return {"source_id": source_id, "path": path, "kind": "text", "page_count": None,
            "engine": "builtin", "warnings": warnings, "blocks": blocks}


def index_pdf_file(path: str, source_id: str) -> dict:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import pdf_text  # noqa: WPS
    except Exception as exc:                                # pragma: no cover - install fault
        return {"source_id": source_id, "path": path, "kind": "pdf", "page_count": None,
                "engine": None, "warnings": [f"pdf_text.py unavailable: {exc}"], "blocks": []}
    res = pdf_text.extract(path)
    blocks = []
    for b in res["blocks"]:
        blocks.append({
            "locator": b["id"],
            "line": None,
            "page": b.get("page"),
            "type": b.get("type") or "body",
            "level": None,
            "section": b.get("section"),
            "text": b["text"],
            "raw": b["text"],
        })
    for p in res["pages"]:
        for b in p["blocks"]:
            if b.get("type") == "heading" and not any(x["locator"] == b["id"] for x in blocks):
                blocks.append({"locator": b["id"], "line": None, "page": p["page"],
                               "type": "heading", "level": None, "section": b.get("section"),
                               "text": b["text"].strip(), "raw": b["text"]})
    order = {b["locator"]: i for i, b in enumerate(blocks)}
    blocks.sort(key=lambda b: (order[b["locator"]],))
    return {"source_id": source_id, "path": path, "kind": "pdf",
            "page_count": res.get("page_count"), "engine": res.get("engine"),
            "warnings": list(res.get("warnings") or []) + list(res.get("engine_errors") or []),
            "blocks": blocks}


def _sections(doc: dict) -> list[dict]:
    out = [{"title": b["text"][:120], "locator": b["locator"], "level": b.get("level")}
           for b in doc["blocks"] if b.get("type") == "heading" and b.get("text")]
    return out


def _guess_meta(doc: dict) -> dict:
    """Only what the blocks actually contain; anything else stays not_reported."""
    meta = {"title": None, "title_locator": None, "authors": None, "venue": None,
            "year": None, "year_locator": None, "doi": None, "doi_locator": None}
    first = [b for b in doc["blocks"] if b.get("text")][:6]
    for b in doc["blocks"]:
        if b.get("type") == "heading" and b.get("level") == 1:
            meta["title"], meta["title_locator"] = b["text"].lstrip("# ").strip(), b["locator"]
            break
    if not meta["title"]:
        for b in first:
            if b.get("page") == 1 and b.get("type") in ("body", "heading") and len(b["text"]) <= 200:
                meta["title"], meta["title_locator"] = b["text"], b["locator"]
                break
    for b in doc["blocks"]:
        m = DOI_RE.search(b.get("text") or "")
        if m:
            meta["doi"], meta["doi_locator"] = m.group(0).rstrip(".,;)"), b["locator"]
            break
    for b in first:
        if b.get("type") == "reference" or CITE_LINE_RE.match(b.get("text") or ""):
            continue
        m = YEAR_RE.search(b.get("text") or "")
        if m:
            meta["year"], meta["year_locator"] = m.group(1), b["locator"]
            break
    return meta


def build_index(root: str) -> dict:
    root = os.path.abspath(root)
    warnings: list[str] = []
    if os.path.isfile(root):
        files = [root]
    elif os.path.isdir(root):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                files.append(os.path.join(dirpath, name))
    else:
        raise FileNotFoundError(root)
    docs, skipped = [], []
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        rel = os.path.relpath(path, root if os.path.isdir(root) else os.path.dirname(root))
        if ext in PDF_EXT:
            docs.append(index_pdf_file(path, rel))
        elif ext in TEXT_EXT:
            docs.append(index_text_file(path, rel))
        else:
            skipped.append({"path": rel, "reason": f"unsupported extension {ext or '(none)'}"})
    multi = len(docs) > 1
    flat = []
    for doc in docs:
        base = os.path.basename(doc.get("path") or "")
        for b in doc["blocks"]:
            # text locators already start with the file name; replace it with the
            # source_id instead of stacking a second copy ("a.md:a.md:1")
            if base and b["locator"].startswith(base + ":"):
                b["tail"] = b["locator"][len(base) + 1:]
            else:
                b["tail"] = b["locator"]
            if multi:
                b["locator"] = f"{doc['source_id']}:{b['tail']}"
        meta = _guess_meta(doc)
        doc["meta"] = meta
        doc["sections"] = _sections(doc)
        flat.extend(dict(b, source_id=doc["source_id"]) for b in doc["blocks"])
    if not docs:
        warnings.append("no readable document found under the given path")
    return {"schema": "docindex/1", "root": root, "multi_source": multi,
            "doc_count": len(docs), "docs": docs, "blocks": flat,
            "skipped": skipped, "warnings": warnings}


def load_index(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != "docindex/1" or "blocks" not in data:
        raise ValueError(f"{path} is not a docindex/1 file; run the index subcommand first")
    return data


def find_block(index: dict, locator: str, source: str | None = None) -> tuple[dict | None, list[str]]:
    candidates = index["blocks"]
    if source:
        candidates = [b for b in candidates if b.get("source_id") == source]
    for b in candidates:
        if b["locator"] == locator:
            return b, []
    tail = [b["locator"] for b in candidates if b["locator"].endswith(":" + locator) or b["locator"].endswith(locator)]
    return None, tail


def cmd_index(a) -> int:
    try:
        data = build_index(a.material)
    except FileNotFoundError:
        print(f"index: no such file or directory: {a.material}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    if a.jsonl:
        with open(a.jsonl, "w", encoding="utf-8") as fh:
            for b in data["blocks"]:
                fh.write(json.dumps({k: v for k, v in b.items() if k != "raw"}, ensure_ascii=False) + "\n")
    print(json.dumps({"docs": data["doc_count"], "blocks": len(data["blocks"]),
                      "skipped": len(data["skipped"]), "warnings": data["warnings"]},
                     ensure_ascii=False))
    return 0


def cmd_search(a) -> int:
    idx = load_index(a.docindex)
    needle = norm(a.query).lower()
    if not needle:
        print("search: empty query", file=sys.stderr)
        return 2
    hits = []
    pattern = re.compile(re.escape(a.query.strip()), re.IGNORECASE)
    for b in idx["blocks"]:
        if a.source and b.get("source_id") != a.source:
            continue
        hay = norm(b.get("text") or "")          # case preserved: context must stay quotable
        m = pattern.search(hay)
        if m is None and norm(a.query) != a.query.strip():
            m = re.compile(re.escape(norm(a.query)), re.IGNORECASE).search(hay)
        if m is None:
            continue
        pos, span = m.start(), m.end() - m.start()
        hits.append({"locator": b["locator"], "source_id": b.get("source_id"),
                     "page": b.get("page"), "type": b.get("type"),
                     "context": hay[max(0, pos - 90):pos + span + 90]})
        if len(hits) >= a.limit:
            break
    if a.json:
        print(json.dumps({"query": a.query, "count": len(hits), "hits": hits}, ensure_ascii=False, indent=1))
    else:
        for h in hits:
            print(f"{h['locator']}\t{h['type']}\t{h['context']}")
        print(f"# {len(hits)} hit(s) for {a.query!r}", file=sys.stderr)
    return 0 if hits else 1


def cmd_show(a) -> int:
    idx = load_index(a.docindex)
    block, tail = find_block(idx, a.locator, a.source)
    if block is None:
        print(f"show: locator {a.locator!r} not in index", file=sys.stderr)
        if tail:
            print("  did you mean: " + ", ".join(sorted(set(tail))[:5]), file=sys.stderr)
        return 2
    payload = block.get("raw")
    if payload is None:
        payload = block.get("text", "")
    sys.stdout.write(payload)
    if not payload.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def cmd_cite(a) -> int:
    idx = load_index(a.docindex)
    block, tail = find_block(idx, a.locator, a.source)
    if block is None:
        print(f"cite: locator {a.locator!r} not in index", file=sys.stderr)
        if tail:
            print("  did you mean: " + ", ".join(sorted(set(tail))[:5]), file=sys.stderr)
        return 2
    doc = next((d for d in idx["docs"] if d["source_id"] == block.get("source_id")), None) or {}
    meta = doc.get("meta") or {}
    title = meta.get("title") or os.path.splitext(os.path.basename(doc.get("path") or ""))[0] or "not_reported"
    authors = meta.get("authors") or "not_reported"
    venue = meta.get("venue") or "not_reported"
    year = meta.get("year") or "not_reported"
    doi = meta.get("doi") or "not_reported"
    line = f"{authors}. {title}. {venue}, {year}. doi:{doi}. [{block['locator']}]"
    print(line)
    print(json.dumps({"locator": block["locator"], "source_id": doc.get("source_id"),
                      "title": title, "authors": authors, "venue": venue, "year": year,
                      "doi": doi, "title_locator": meta.get("title_locator"),
                      "year_locator": meta.get("year_locator"), "doi_locator": meta.get("doi_locator"),
                      "verbatim": block.get("text", "")[:300]}, ensure_ascii=False, indent=1))
    return 0


def cmd_digest(a) -> int:
    idx = load_index(a.docindex)
    out = []
    for doc in idx["docs"]:
        heads = doc.get("sections") or []
        body = [b for b in doc["blocks"] if b.get("type") != "heading"]
        entry = {"source_id": doc["source_id"], "path": doc.get("path"), "kind": doc.get("kind"),
                 "pages": doc.get("page_count"), "engine": doc.get("engine"),
                 "blocks": len(doc["blocks"]), "body_blocks": len(body),
                 "meta": doc.get("meta"), "warnings": doc.get("warnings") or [],
                 "sections": []}
        if heads:
            for i, h in enumerate(heads):
                stop = heads[i + 1]["locator"] if i + 1 < len(heads) else None
                start_i = next(j for j, b in enumerate(doc["blocks"]) if b["locator"] == h["locator"])
                end_i = (next(j for j, b in enumerate(doc["blocks"]) if b["locator"] == stop)
                         if stop else len(doc["blocks"]))
                entry["sections"].append({"title": h["title"], "locator": h["locator"],
                                          "span_blocks": max(0, end_i - start_i - 1)})
        else:
            entry["note"] = "no heading detected; locate content with search instead of sections"
        out.append(entry)
    text = json.dumps({"docs": out, "skeleton_only": True}, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"digest: {a.out}")
    else:
        print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Reading client over a material directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="build docindex.json")
    p.add_argument("material", help="a directory or a single file")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--jsonl")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="find a phrase, print locators")
    p.add_argument("docindex")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--source")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="print the block at a locator, verbatim")
    p.add_argument("docindex")
    p.add_argument("locator")
    p.add_argument("--source")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("cite", help="one citation line for a locator")
    p.add_argument("docindex")
    p.add_argument("locator")
    p.add_argument("--source")
    p.set_defaults(func=cmd_cite)

    p = sub.add_parser("digest", help="per-section skeleton with locators")
    p.add_argument("docindex")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_digest)

    a = ap.parse_args()
    try:
        return a.func(a)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{a.cmd}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
