#!/usr/bin/env python3
"""Turn a supplied manuscript into sectioned, locator-addressed working blocks.

Why this exists: the case workorder names edit targets by paragraph id
(`C01-P002`) and requires that original text be preserved byte-for-byte. That is
only checkable if every paragraph has a stable id *before* anyone edits, and if
the id scheme is reproducible from the source file.

Nothing here synthesises prose. If the source cannot be found or parsed, the
result says so (`source_unavailable` / `parse_failed`) and `blocks` stays empty;
a downstream step must then report `insufficient_evidence`, not invent chapters.

CLI:
  python3 ingest.py discover --case /input/case.json -o candidates.json
  python3 ingest.py build    --case /input/case.json [--source PATH]... -o blocks.json
  python3 ingest.py show     blocks.json [--id C01-P002]
  python3 ingest.py check    blocks.json            # id uniqueness / empty / dup text
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

TEXT_EXT = {".md", ".markdown", ".txt", ".text"}
DOCX_EXT = {".docx"}
PDF_EXT = {".pdf"}

# Files that are case scaffolding rather than the manuscript itself.
SKIP_NAMES = {"case.json", "task.md", "output-contract.md", "result.json", "blocks.json",
              "state.json", "receipts.jsonl", "candidates.json", "manifest.json",
              "environment.yaml", "resources.yaml"}

PATH_KEYS = ("files", "materials", "manuscript", "chapters", "attachments",
             "input_files", "source_files", "documents", "paths")

# An explicit id marker in the source wins over a derived sequential id.
ID_MARKER_RE = re.compile(r"^\s*(?:#{1,6}\s*)?\[(?P<id>[A-Za-z][\w-]*?-P\d{2,4})\]\s*")
FM_ID_RE = re.compile(r"^\s*id\s*:\s*(?P<id>[A-Za-z][\w-]*?-P\d{2,4})\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
CAPTION_RE = re.compile(r"^\s*(?:Table|Figure|Fig\.|Tab\.|表|图)\s*\d+", re.I)
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- discovery

def _walk_paths(obj, found, where=""):
    """Collect string values under keys that name files/materials."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{where}.{key}" if where else key
            if key.lower() in PATH_KEYS:
                candidates = value if isinstance(value, list) else [value]
                for item in candidates:
                    if isinstance(item, str):
                        found.append((here, item))
                    elif isinstance(item, dict):
                        for k in ("path", "file", "name", "url"):
                            if isinstance(item.get(k), str):
                                found.append((f"{here}.{k}", item[k]))
            else:
                _walk_paths(value, found, here)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_paths(item, found, f"{where}[{i}]")


def discover(case_path: str | None, extra_dirs: list[str] | None = None) -> dict:
    """Find candidate manuscript files and say exactly where each came from."""
    hits: list[dict] = []
    seen: set[str] = set()

    def add(path: str, via: str):
        if not path:
            return
        if path.startswith(("http://", "https://")):
            hits.append({"path": path, "via": via, "exists": False,
                         "note": "remote url; not fetched (offline task forbids network)"})
            return
        real = os.path.realpath(path)
        key = os.path.realpath(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        exists = os.path.isfile(path)
        entry = {"path": os.path.abspath(path), "via": via, "exists": exists}
        if exists:
            entry["ext"] = os.path.splitext(path)[1].lower()
            entry["bytes"] = os.path.getsize(path)
            entry["sha256"] = sha256_file(path)
        hits.append(entry)

    if case_path and os.path.isfile(case_path):
        case = json.load(open(case_path, encoding="utf-8"))
        refs: list[tuple[str, str]] = []
        _walk_paths(case, refs)
        for where, path in refs:
            base = os.path.dirname(os.path.abspath(case_path))
            if not os.path.isabs(path):
                add(os.path.join(base, path), f"case:{where}")
            else:
                add(path, f"case:{where}")

    dirs = list(extra_dirs or [])
    if case_path:
        dirs.append(os.path.dirname(os.path.abspath(case_path)))
    dirs += ["/input", "/app/work/input", "/app/work", "/app/input"]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            low = name.lower()
            if low in SKIP_NAMES or low.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXT | DOCX_EXT | PDF_EXT:
                add(os.path.join(d, name), f"scan:{d}")

    usable = [h for h in hits if h.get("exists") and h.get("ext") in TEXT_EXT | DOCX_EXT | PDF_EXT]
    return {"case": case_path, "candidates": hits, "usable": usable,
            "status": "found" if usable else "source_unavailable"}


# ---------------------------------------------------------------- extraction

def _docx_paragraphs(path: str) -> list[tuple[str, str]]:
    """(kind, text) pairs in document order from word/document.xml."""
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n == "word/document.xml"]
        if not names:
            raise ValueError("docx has no word/document.xml")
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S):
        style = re.search(r'<w:pStyle w:val="([^"]+)"', para)
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        text = "".join(texts)
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
        if not text.strip():
            continue
        kind = "heading" if style and re.match(r"(?i)heading|title", style.group(1)) else "body"
        out.append((kind, text.strip()))
    return out


def _text_paragraphs(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if buf:
                out.append(("body", "\n".join(buf).strip()))
                buf = []
            continue
        m = HEADING_RE.match(line)
        if m:
            if buf:
                out.append(("body", "\n".join(buf).strip()))
                buf = []
            out.append(("heading%d" % len(m.group(1)), m.group(2).strip()))
            continue
        if TABLE_ROW_RE.match(line):
            if buf:
                out.append(("body", "\n".join(buf).strip()))
                buf = []
            out.append(("table", line.strip()))
            continue
        buf.append(line)
    if buf:
        out.append(("body", "\n".join(buf).strip()))
    return [p for p in out if p[1]]


def _pdf_paragraphs(path: str, scripts_dir: str) -> list[tuple[str, str]]:
    sys.path.insert(0, scripts_dir)
    import pdf_text  # noqa: E402  (same directory as this script)
    doc = pdf_text.extract(path)
    out = []
    for b in doc.get("blocks") or []:
        kind = b.get("type") or "body"
        if kind == "furniture":
            continue
        out.append((kind, (b.get("text") or "").strip(), b.get("id")))
    return out


def build_blocks(paths: list[str], case_id: str | None,
                 scripts_dir: str | None = None) -> dict:
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    prefix = (case_id or "SRC").strip() or "SRC"
    blocks: list[dict] = []
    warnings: list[str] = []
    sources: list[dict] = []
    used_ids: set[str] = set()
    seq = 0
    section = None

    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in TEXT_EXT:
                items = _text_paragraphs(open(path, encoding="utf-8", errors="replace").read())
                fmt = "text"
            elif ext in DOCX_EXT:
                items = _docx_paragraphs(path)
                fmt = "docx"
            elif ext in PDF_EXT:
                items = _pdf_paragraphs(path, scripts_dir)
                fmt = "pdf"
            else:
                warnings.append(f"unsupported extension {ext!r}: {path}")
                continue
        except Exception as exc:  # noqa: BLE001 - report, never guess content
            warnings.append(f"parse_failed {path}: {type(exc).__name__}: {exc}")
            sources.append({"path": path, "format": ext.lstrip("."), "status": "parse_failed",
                            "error": f"{type(exc).__name__}: {exc}"})
            continue

        sources.append({"path": path, "format": fmt, "status": "parsed",
                        "sha256": sha256_file(path), "bytes": os.path.getsize(path)})
        for item in items:
            kind, text = item[0], item[1]
            explicit = None
            if len(item) > 2 and item[2]:
                explicit = item[2]
            m = ID_MARKER_RE.match(text)
            if m:
                explicit = m.group("id")
                text = text[m.end():].strip()
            elif FM_ID_RE.match(text):
                explicit = FM_ID_RE.match(text).group("id")
                continue
            if kind.startswith("heading"):
                section = text
            seq += 1
            bid = explicit or f"{prefix}-P{seq:03d}"
            if bid in used_ids:
                warnings.append(f"duplicate id {bid!r} in {os.path.basename(path)}; suffixed")
                n = 2
                while f"{bid}-{n}" in used_ids:
                    n += 1
                bid = f"{bid}-{n}"
            used_ids.add(bid)
            blocks.append({
                "id": bid,
                "locator": bid,
                "source": os.path.basename(path),
                "section": section,
                "kind": "heading" if kind.startswith("heading") else kind,
                "text": text,
                "sha256": sha256_text(text),
            })

    if not blocks:
        status = "source_unavailable" if not sources else "parse_failed"
    else:
        status = "ok" if not warnings else "partial"
    return {"status": status, "case_id": case_id, "sources": sources,
            "warnings": warnings, "n_blocks": len(blocks),
            "id_scheme": f"{prefix}-P<nnn> unless the source carries an explicit [id] marker",
            "blocks": blocks}


# ---------------------------------------------------------------- checks

def check_blocks(payload: dict) -> dict:
    blocks = payload.get("blocks") or []
    problems, notes = [], []
    ids = [b.get("id") for b in blocks]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        problems.append(f"duplicate block ids: {dups[:10]}")
    if not blocks:
        problems.append("no blocks: the source was not found or not parsed")
    empty = [b.get("id") for b in blocks if not (b.get("text") or "").strip()]
    if empty:
        problems.append(f"empty block text: {empty[:10]}")
    norm = {}
    for b in blocks:
        key = " ".join((b.get("text") or "").split()).lower()
        if len(key) > 40:
            norm.setdefault(key, []).append(b.get("id"))
    repeated = {k[:60]: v for k, v in norm.items() if len(v) > 1}
    if repeated:
        notes.append(f"identical paragraph text appears more than once: {list(repeated.items())[:5]}")
    if not any(b.get("kind") == "heading" for b in blocks):
        notes.append("no headings detected: section attribution will be coarse")
    return {"n_blocks": len(blocks), "problems": problems, "notes": notes,
            "status": payload.get("status"),
            "warnings": payload.get("warnings") or []}


def main() -> int:
    ap = argparse.ArgumentParser(description="Manuscript ingest with stable paragraph ids")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover")
    d.add_argument("--case", default=None)
    d.add_argument("--dir", action="append", default=None)
    d.add_argument("-o", "--out", default=None)

    b = sub.add_parser("build")
    b.add_argument("--case", default=None)
    b.add_argument("--source", action="append", default=None)
    b.add_argument("--dir", action="append", default=None)
    b.add_argument("--case-id", default=None)
    b.add_argument("-o", "--out", required=True)

    s = sub.add_parser("show")
    s.add_argument("blocks")
    s.add_argument("--id", default=None)

    c = sub.add_parser("check")
    c.add_argument("blocks")

    a = ap.parse_args()

    if a.cmd == "discover":
        payload = discover(a.case, a.dir)
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
            print(json.dumps({"out": a.out, "status": payload["status"],
                              "usable": [h["path"] for h in payload["usable"]]},
                             ensure_ascii=False))
        else:
            print(text)
        return 0 if payload["status"] == "found" else 1

    if a.cmd == "build":
        case_id = a.case_id
        case_payload = None
        if a.case and os.path.isfile(a.case):
            case_payload = json.load(open(a.case, encoding="utf-8"))
            case_id = case_id or case_payload.get("case_id")
        paths = list(a.source or [])
        if not paths:
            disc = discover(a.case, a.dir)
            paths = [h["path"] for h in disc["usable"]]
        payload = build_blocks(paths, case_id)
        if case_payload is not None:
            payload["workorder"] = case_payload.get("organizer_edit_workorder")
        open(a.out, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
        print(json.dumps({"out": a.out, "status": payload["status"], "n_blocks": payload["n_blocks"],
                          "sources": [s["path"] for s in payload["sources"]],
                          "warnings": payload["warnings"]}, ensure_ascii=False))
        return 0 if payload["blocks"] else 1

    if a.cmd == "show":
        payload = json.load(open(a.blocks, encoding="utf-8"))
        blocks = payload.get("blocks") or []
        if a.id:
            blocks = [b for b in blocks if b.get("id") == a.id]
        print(json.dumps(blocks, ensure_ascii=False, indent=1))
        return 0 if blocks else 1

    payload = json.load(open(a.blocks, encoding="utf-8"))
    report = check_blocks(payload)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
