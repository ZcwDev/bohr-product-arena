#!/usr/bin/env python3
"""Offline source inventories, fact-table exports, and grounded citations.

The public entry point is ``build_source_bundle(case, index, outdir)``.  The
companion command is::

    python3 sources.py build --case CASE --index DOCINDEX --outdir OUTPUT_DIR

``index`` is normally docindex/1 produced by reader.py.  PDF captions and
bibliographic fields are accepted only when they can be tied back to text
blocks in that index.  Delimited files, Markdown pipe tables, and XLSX
worksheets are read from their actual bytes; no network service is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import time
import unicodedata
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

MISSING = "not_reported"
UNAVAILABLE = "not_available"
TIME_BUDGET_SECONDS = 100.0
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 48 * 1024 * 1024
MAX_XLSX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_TABLE_ROWS = 100000
MAX_CELL_TEXT = 4 * 1024 * 1024
MAX_WARNINGS = 500
TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".md", ".markdown"}
SCAN_SUFFIXES = TABLE_SUFFIXES | {".pdf"}
PATH_KEYS = (
    "path", "file", "file_path", "filepath", "filename", "local_path",
    "absolute_path", "relative_path", "material_path", "materials_dir",
)
CONTAINER_KEYS = {
    "source", "sources", "materials", "files", "documents", "document",
    "primary", "primary_document", "main_document", "paper",
    "supplement", "supplements", "supplementary", "supplementary_reading",
    "electronic_information", "electronic_supplementary_information",
    "supporting_information", "additional_reading", "materials_dir",
}
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
YEAR_RE = re.compile(r"(?<!\d)(?:1[89]\d{2}|20\d{2})(?!\d)")
TABLE_RE = re.compile(
    r"^\s*(?:(?:supplementary|supplement|supporting)\s+)?"
    r"(?:table|tab\.?|表)\s*"
    r"([A-Za-z]?\d+(?:[.\-]\d+)*[A-Za-z]?|[IVXLCDM]+)(?=$|[\s.．。:：、)）\-])",
    re.IGNORECASE,
)
MD_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "\u00a0": " ",
}
SOURCE_FIELDS = ["source_id", "path", "title", "kind", "sha256", "bytes", "notes"]
TABLE_FIELDS = [
    "table_id", "title", "source_file", "sheet", "csv_file",
    "caption_locator", "caption_verbatim", "mapping_method", "confidence", "notes",
]
BIBLIO_FIELDS = [
    "source_id", "title", "authors", "journal", "year", "doi",
    "source_file", "title_locator", "authors_locator", "journal_locator",
    "year_locator", "doi_locator", "notes",
]


class _BudgetExceeded(Exception):
    pass


class _Context:
    def __init__(self, initial_warnings: Optional[List[dict]] = None) -> None:
        self.started = time.monotonic()
        self.deadline = self.started + TIME_BUDGET_SECONDS
        self.warnings: List[dict] = list(initial_warnings or [])

    def checkpoint(self) -> None:
        if time.monotonic() >= self.deadline:
            raise _BudgetExceeded("internal processing time budget exhausted")

    def warn(self, code: str, message: str, path: Optional[str] = None,
             operation: Optional[str] = None, **extra: Any) -> None:
        if len(self.warnings) >= MAX_WARNINGS:
            if len(self.warnings) == MAX_WARNINGS:
                self.warnings.append({
                    "code": "warnings_truncated",
                    "message": "additional warnings were omitted",
                })
            return
        item: Dict[str, Any] = {"code": code, "message": str(message)[:800]}
        if path:
            item["path"] = str(path)
        if operation:
            item["operation"] = operation
        for key, value in extra.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                item[key] = value
        self.warnings.append(item)


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()


def _reported(value: Any) -> Any:
    if value is None or value == "":
        return MISSING
    if isinstance(value, (list, tuple)):
        vals = [_text(v) for v in value if _text(v)]
        return "; ".join(vals) if vals else MISSING
    if isinstance(value, dict):
        return MISSING
    return str(value)


def normalize_text(value: Any) -> str:
    """Normalize for comparison without changing stored verbatim source text."""
    text = _text(value)
    text = "".join(LIGATURES.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def _comparison_key(value: Any) -> str:
    text = normalize_text(value).casefold()
    text = re.sub(r"\s*([.．。:：、;；,，])\s*", r"\1", text)
    return text.strip(" .．。:：;；")


def _compact(value: Any, limit: int = 500) -> str:
    return normalize_text(value)[:limit]


def _load_json(path: str, label: str, warnings: List[dict]) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            warnings.append({
                "code": "invalid_" + label,
                "path": path,
                "message": label + " JSON must contain an object",
            })
            return {}
        return data
    except Exception as exc:  # input errors must still produce an honest bundle
        warnings.append({
            "code": "unreadable_" + label,
            "path": path,
            "operation": "json_load",
            "message": str(exc),
        })
        return {}


def _safe_read(path: str, limit: int, ctx: _Context, operation: str) -> bytes:
    ctx.checkpoint()
    chunks: List[bytes] = []
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("%s exceeds the %d-byte read limit" % (operation, limit))
            ctx.checkpoint()
    return b"".join(chunks)


def _decode(raw: bytes, path: str, ctx: _Context) -> str:
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        ctx.warn("text_decode_replaced",
                 "text is not valid UTF-8; undecodable bytes were replaced",
                 path, "text_decode")
        return raw.decode("utf-8-sig", errors="replace")


def _index_root(index: dict) -> str:
    root = _text(index.get("root"))
    if not root:
        return os.getcwd()
    root = os.path.abspath(os.path.expanduser(root))
    if os.path.isdir(root):
        return root
    if os.path.splitext(root)[1]:
        return os.path.dirname(root) or os.getcwd()
    return root


def _resolve_path(value: Any, bases: Sequence[str]) -> str:
    raw = _text(value)
    if not raw:
        return ""
    raw = os.path.expanduser(raw)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw):
        return raw
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    for base in bases:
        if not base:
            continue
        candidate = os.path.abspath(os.path.join(base, raw))
        if os.path.exists(candidate):
            return candidate
    base = bases[0] if bases else os.getcwd()
    return os.path.abspath(os.path.join(base, raw))


def _descriptor_roles(descriptor: Any, trail: Sequence[str]) -> List[str]:
    roles = [str(part) for part in trail]
    if isinstance(descriptor, dict):
        for key in ("role", "category", "type", "kind", "relation"):
            value = descriptor.get(key)
            if isinstance(value, str):
                roles.append(value)
            elif isinstance(value, list):
                roles.extend(str(v) for v in value if isinstance(v, (str, int)))
    return roles


def _case_path_entries(case: dict, bases: Sequence[str], ctx: _Context) -> List[dict]:
    """Read file references only from structural file/material containers.

    Bibliographic title, DOI, and task text are deliberately not treated as
    paths.  Roles come from the input structure, never from a filename label.
    """
    found: List[dict] = []
    seen_nodes = set()
    visits = [0]

    def scan(node: Any, trail: Tuple[str, ...], explicit: bool) -> None:
        visits[0] += 1
        if visits[0] > 1000 or len(trail) > 12:
            ctx.warn("case_structure_limit", "case material traversal limit reached", operation="discover")
            return
        ctx.checkpoint()
        if isinstance(node, str):
            looks_like_file = os.path.splitext(node)[1].lower() in SCAN_SUFFIXES
            if explicit or looks_like_file:
                path = _resolve_path(node, bases)
                if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path):
                    found.append({"path": path, "descriptor": None,
                                  "roles": list(trail), "explicit": True})
            return
        identity = id(node)
        if identity in seen_nodes:
            return
        seen_nodes.add(identity)
        if isinstance(node, list):
            for value in node:
                scan(value, trail, explicit)
            return
        if not isinstance(node, dict):
            return
        handled = set()
        for key in PATH_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                found.append({"path": _resolve_path(value, bases), "descriptor": node,
                              "roles": _descriptor_roles(node, trail), "explicit": True})
                handled.add(key)
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered in handled:
                continue
            if lowered in CONTAINER_KEYS:
                scan(value, trail + (lowered,), True)
            elif explicit and isinstance(value, (dict, list)):
                scan(value, trail + (lowered,), True)
            elif explicit and isinstance(value, str) and os.path.splitext(value)[1].lower() in SCAN_SUFFIXES:
                scan(value, trail + (lowered,), True)

    for key in ("source", "materials"):
        if key in case:
            scan(case[key], (key,), key == "materials")
    return found


def _expand_directories(entries: List[dict], outdir: str, ctx: _Context) -> List[dict]:
    expanded: List[dict] = []
    output_root = os.path.realpath(outdir)
    for entry in entries:
        path = entry["path"]
        if not os.path.isdir(path):
            expanded.append(entry)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            ctx.checkpoint()
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d not in {"__pycache__", "node_modules"})
            if os.path.realpath(dirpath) == output_root:
                dirnames[:] = []
                continue
            for name in sorted(filenames):
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in SCAN_SUFFIXES:
                    continue
                full = os.path.join(dirpath, name)
                if os.path.realpath(full).startswith(output_root + os.sep):
                    continue
                item = dict(entry)
                item["path"] = os.path.abspath(full)
                expanded.append(item)
    return expanded


def _doc_list(index: dict, ctx: _Context) -> List[dict]:
    raw: Any = index.get("docs")
    if not isinstance(raw, (list, dict)):
        raw = index.get("documents", index.get("sources", []))
    if isinstance(raw, dict):
        values = []
        for key, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("source_id", str(key))
                values.append(item)
        raw = values
    if not isinstance(raw, list):
        ctx.warn("invalid_index_docs", "index docs/sources is not a list or object", operation="discover")
        return []
    docs = []
    for position, value in enumerate(raw):
        if not isinstance(value, dict):
            ctx.warn("invalid_index_doc", "ignored a non-object document entry",
                     operation="discover", position=position)
            continue
        docs.append(value)
    if not docs and isinstance(index.get("path"), str):
        docs.append(index)
    return docs


def _flat_blocks(index: dict) -> List[dict]:
    blocks = index.get("blocks", [])
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []


def _doc_blocks(doc: dict, flat: Sequence[dict], source_id: str) -> List[dict]:
    raw = doc.get("blocks", [])
    blocks = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
    if blocks:
        return blocks
    return [b for b in flat if _text(b.get("source_id")) == source_id]


def _block_locator(block: dict) -> str:
    for key in ("locator", "id", "block_id"):
        value = _text(block.get(key))
        if value:
            return value
    return ""


def _find_meta_block(meta_value: Any, locator: Any, blocks: Sequence[dict],
                     contains: bool = False) -> Optional[dict]:
    wanted = _comparison_key(meta_value)
    loc = _text(locator)
    if not wanted:
        return None
    if loc:
        for block in blocks:
            if _block_locator(block) == loc or _text(block.get("tail")) == loc:
                haystack = _comparison_key(block.get("text", block.get("raw", "")))
                if (wanted in haystack if contains else wanted == haystack.lstrip("# ")):
                    return block
                prefixed = "#" + wanted
                if not contains and prefixed == _comparison_key(block.get("text", "")):
                    return block
                return None
    matches = []
    for block in blocks:
        if block.get("type") in {"reference", "furniture"}:
            continue
        haystack = _comparison_key(block.get("text", block.get("raw", "")))
        if (wanted in haystack if contains else wanted == haystack.lstrip("# ")):
            matches.append(block)
    return matches[0] if len(matches) == 1 else None


def _validated_biblio(doc: dict, blocks: Sequence[dict]) -> dict:
    raw_meta = doc.get("meta", {})
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    result: Dict[str, Any] = {
        "title": MISSING, "authors": MISSING, "journal": MISSING,
        "year": MISSING, "doi": MISSING,
        "title_locator": MISSING, "authors_locator": MISSING,
        "journal_locator": MISSING, "year_locator": MISSING,
        "doi_locator": MISSING,
    }
    aliases = {
        "title": ("title", "title_locator"),
        "authors": ("authors", "authors_locator"),
        "journal": ("venue", "venue_locator"),
        "year": ("year", "year_locator"),
        "doi": ("doi", "doi_locator"),
    }
    for field, (key, default_loc) in aliases.items():
        value = meta.get(key)
        if field == "journal" and not value:
            value = meta.get("journal")
        if value is None or value == "" or value == []:
            continue
        locator = meta.get(default_loc) or meta.get(field + "_locator")
        block = _find_meta_block(value, locator, blocks, contains=field != "title")
        if field == "authors" and isinstance(value, list):
            block = _find_meta_block("; ".join(_text(v) for v in value), locator, blocks, contains=True)
            if block is None and locator:
                block = next((b for b in blocks if _block_locator(b) == _text(locator)), None)
        if block is None and field == "authors" and locator:
            block = next((b for b in blocks if _block_locator(b) == _text(locator)), None)
        if block is not None:
            result[field] = _reported(value)
            result[field + "_locator"] = locator if _text(locator) else _block_locator(block)
    if result["doi"] == MISSING:
        found = []
        for block in list(blocks)[:20]:
            if block.get("type") in {"reference", "furniture"}:
                continue
            match = DOI_RE.search(_text(block.get("text")))
            if match:
                found.append((match.group(0).rstrip(".,;)"), block))
        distinct = {value for value, _ in found}
        if len(distinct) == 1 and found:
            result["doi"] = found[0][0]
            result["doi_locator"] = _block_locator(found[0][1]) or MISSING
    return result


def _is_pdf(entry: dict) -> bool:
    doc = entry.get("doc") or {}
    return (_text(doc.get("kind")).lower() == "pdf" or
            os.path.splitext(entry.get("path", ""))[1].lower() == ".pdf")


def _hash_file(path: str, ctx: _Context) -> Tuple[Any, Any, List[str]]:
    notes: List[str] = []
    try:
        ctx.checkpoint()
        size = os.path.getsize(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                ctx.checkpoint()
        return digest.hexdigest(), size, notes
    except Exception as exc:
        ctx.warn("file_hash_failed", str(exc), path, "sha256")
        notes.append("file bytes unavailable: " + str(exc)[:180])
        return UNAVAILABLE, UNAVAILABLE, notes


def _inventory(case: dict, index: dict, outdir: str, ctx: _Context) -> Tuple[List[dict], List[dict]]:
    base = _index_root(index)
    bases = [base, os.getcwd()]
    declared = _expand_directories(_case_path_entries(case, bases, ctx), outdir, ctx)
    by_path: Dict[str, dict] = {}
    order: List[str] = []

    def add(path: str, source_id: str = "", doc: Optional[dict] = None,
            descriptor: Any = None, roles: Optional[Sequence[str]] = None) -> None:
        if not path or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path):
            if path:
                ctx.warn("remote_path_ignored", "remote references are not read by the offline builder", path)
            return
        path = os.path.abspath(path)
        key = os.path.realpath(path)
        if key not in by_path:
            by_path[key] = {"path": path, "source_id": source_id or "", "doc": doc,
                            "descriptor": descriptor, "extra_descriptors": [],
                            "roles": list(roles or []), "notes": [], "table_count": 0}
            order.append(key)
        else:
            item = by_path[key]
            item["roles"].extend(list(roles or []))
            if item.get("doc") is None and doc is not None:
                item["doc"] = doc
                item["source_id"] = source_id or item["source_id"]
            if item.get("descriptor") is None and descriptor is not None:
                item["descriptor"] = descriptor
            elif descriptor is not None and descriptor is not item.get("descriptor"):
                item.setdefault("extra_descriptors", []).append(descriptor)

    flat = _flat_blocks(index)
    for position, doc in enumerate(_doc_list(index, ctx), start=1):
        path = _resolve_path(doc.get("path"), bases)
        if not path:
            ctx.warn("index_doc_without_path", "indexed document has no usable path",
                     operation="discover", position=position)
            continue
        source_id = _text(doc.get("source_id")) or "source_%03d" % position
        add(path, source_id, doc, doc, ["index"])
    for entry in declared:
        add(entry["path"], descriptor=entry.get("descriptor"), roles=entry.get("roles"))

    entries = [by_path[key] for key in order]
    used_ids: Dict[str, int] = {}
    for position, entry in enumerate(entries, start=1):
        sid = entry["source_id"] or "source_%03d" % position
        used_ids[sid] = used_ids.get(sid, 0) + 1
        if used_ids[sid] > 1:
            ctx.warn("duplicate_source_id", "source_id was disambiguated with a numeric suffix",
                     entry["path"], source_id=sid)
            sid = sid + "__" + str(used_ids[sid])
        entry["source_id"] = sid
        doc = entry.get("doc") or {}
        blocks = _doc_blocks(doc, flat, _text(doc.get("source_id")) or sid)
        entry["blocks"] = blocks
        entry["biblio"] = _validated_biblio(doc, blocks)
        entry["sha256"], entry["bytes"], hash_notes = _hash_file(entry["path"], ctx)
        entry["notes"].extend(hash_notes)
        for warning in doc.get("warnings", []) if isinstance(doc.get("warnings"), list) else []:
            entry["notes"].append("index: " + _compact(warning, 240))

    pdf_entries = [entry for entry in entries if _is_pdf(entry)]
    source = case.get("source", {})
    case_title = _text(source.get("title")) if isinstance(source, dict) else ""
    title_matches = [entry for entry in pdf_entries
                     if case_title and entry["biblio"]["title"] != MISSING and
                     _comparison_key(case_title) == _comparison_key(entry["biblio"]["title"])]
    primary: Optional[dict] = None
    explicit_primary = [entry for entry in entries
                        if _role(entry["roles"]) == "primary_document"]
    if len(explicit_primary) == 1:
        primary = explicit_primary[0]
    elif len(title_matches) == 1:
        primary = title_matches[0]
    elif len(pdf_entries) == 1:
        primary = pdf_entries[0]
    for entry in entries:
        role = _role(entry["roles"])
        ext = os.path.splitext(entry["path"])[1].lower()
        if entry is primary:
            kind = "primary_document"
            entry["notes"].append("primary document identified from explicit role, unique indexed title, or sole PDF")
        elif role:
            kind = role
        elif ext in {".csv", ".tsv", ".xlsx"}:
            kind = "fact_table"
        elif _is_pdf(entry) and primary is not None:
            kind = "supplementary_reading"
            entry["notes"].append("non-primary PDF; no more specific role was reported")
        elif _is_pdf(entry):
            kind = "document_unresolved"
        elif ext in {".md", ".markdown"}:
            kind = "material"
        else:
            kind = "other"
        entry["kind"] = kind
    if pdf_entries and primary is None:
        ctx.warn("primary_document_unresolved",
                 "multiple PDFs are present but no primary document can be identified uniquely",
                 operation="classify")
    return entries, flat


def _role(roles: Sequence[str]) -> str:
    joined = " ".join(roles).lower().replace("-", "_")
    if re.search(r"\b(primary|main|main_document|main paper|article)\b", joined):
        return "primary_document"
    if re.search(r"electronic.{0,24}(information|supplement)|\besi\b|supporting_information", joined):
        return "electronic_information"
    if re.search(r"supplement|additional_reading|supporting_reading", joined):
        return "supplementary_reading"
    if re.search(r"\bmaterials?\b", joined):
        return "material"
    return ""


def _read_source_text(path: str, ctx: _Context) -> str:
    raw = _safe_read(path, MAX_TEXT_BYTES, ctx, "text material")
    text = _decode(raw, path, ctx)
    return text


def _metadata_hints(entry: dict) -> List[str]:
    descriptors = [entry.get("descriptor")] + list(entry.get("extra_descriptors", []))
    hints: List[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            continue
        for key in ("caption", "table_caption", "table_title", "table_label", "label", "title", "table"):
            value = descriptor.get(key)
            if isinstance(value, str) and value.strip():
                hints.append(value.strip())
            elif isinstance(value, dict):
                for subkey in ("caption", "title", "label", "name"):
                    if isinstance(value.get(subkey), str):
                        hints.append(value[subkey].strip())
    return _dedupe(hints)


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        key = _comparison_key(value)
        if key and key not in seen:
            seen.add(key)
            out.append(_text(value))
    return out


def _caption_hints(text: str) -> List[str]:
    hints = []
    for raw in text.splitlines()[:30]:
        line = raw.strip().lstrip("#>- ").strip()
        if not line:
            continue
        match = TABLE_RE.match(line)
        if match:
            hints.append(line)
            continue
        labelled = re.match(r"^(?:caption|table caption|题注|表题)\s*[:：,，\t]\s*(.+)$", line, re.I)
        if labelled:
            hints.append(labelled.group(1).strip())
    return _dedupe(hints)


def _bounded_rows(rows: Iterable[List[Any]], path: str, ctx: _Context) -> Tuple[List[List[str]], List[str]]:
    out: List[List[str]] = []
    notes: List[str] = []
    for position, row in enumerate(rows, start=1):
        ctx.checkpoint()
        values = []
        for cell in row:
            value = "" if cell is None else str(cell)
            if len(value) > MAX_CELL_TEXT:
                raise ValueError("a cell exceeds the text size limit")
            values.append(value)
        out.append(values)
        if position >= MAX_TABLE_ROWS:
            notes.append("row limit reached; exported rows are a partial extraction")
            ctx.warn("table_row_limit", "table extraction stopped at the row limit", path)
            break
    return out, notes


def _parse_delimited(entry: dict, ctx: _Context) -> List[dict]:
    path = entry["path"]
    text = _read_source_text(path, ctx)
    delimiter = "\t" if path.lower().endswith((".tsv", ".tab")) else ","
    descriptor = entry.get("descriptor") if isinstance(entry.get("descriptor"), dict) else {}
    override = descriptor.get("delimiter")
    if isinstance(override, str) and override:
        delimiter = {"tab": "\t", "\\t": "\t", "comma": ","}.get(override, override)
        if len(delimiter) != 1:
            raise ValueError("delimiter override must be one character")
    try:
        csv.field_size_limit(max(csv.field_size_limit(), MAX_CELL_TEXT))
    except (OverflowError, AttributeError):
        pass
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows, notes = _bounded_rows(reader, path, ctx)
    hints = _dedupe(_metadata_hints(entry) + _caption_hints(text))
    return [{"entry": entry, "sheet": "not_applicable", "rows": rows,
             "hints": hints, "notes": notes, "source_locator": MISSING}]


def _split_md_row(line: str) -> List[str]:
    stripped = line.strip()
    escaped = False
    code = False
    cells: List[str] = []
    current: List[str] = []
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
            current.append(char)
        elif char == "`":
            code = not code
            current.append(char)
        elif char == "|" and not code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return [re.sub(r"\\\|", "|", cell) for cell in cells]


def _markdown_tables(text: str) -> List[dict]:
    lines = text.splitlines()
    tables = []
    position = 0
    while position + 1 < len(lines):
        if "|" not in lines[position] or "|" not in lines[position + 1]:
            position += 1
            continue
        header = _split_md_row(lines[position])
        separators = _split_md_row(lines[position + 1])
        if not header or len(header) != len(separators) or not all(MD_SEPARATOR_RE.match(c.replace(" ", "")) for c in separators):
            position += 1
            continue
        start = position
        rows = [header]
        position += 2
        while position < len(lines) and "|" in lines[position] and lines[position].strip():
            row = _split_md_row(lines[position])
            if len(row) == len(header) and all(MD_SEPARATOR_RE.match(c.replace(" ", "")) for c in row):
                break
            rows.append(row)
            position += 1
        hints = []
        for prior in lines[max(0, start - 4):start]:
            candidate = prior.strip().lstrip("#>- ").strip()
            if TABLE_RE.match(candidate):
                hints.append(candidate)
            labelled = re.match(r"^(?:caption|table caption|题注|表题)\s*[:：,，]\s*(.+)$", candidate, re.I)
            if labelled:
                hints.append(labelled.group(1))
        tables.append({"rows": rows, "hints": _dedupe(hints), "line": start + 1})
    return tables


def _parse_markdown(entry: dict, ctx: _Context) -> List[dict]:
    path = entry["path"]
    text = _read_source_text(path, ctx)
    result = []
    for table in _markdown_tables(text):
        rows, notes = _bounded_rows(table["rows"], path, ctx)
        result.append({"entry": entry, "sheet": "not_applicable", "rows": rows,
                       "hints": _dedupe(_metadata_hints(entry) + table["hints"] + _caption_hints(text)),
                       "notes": notes,
                       "source_locator": os.path.basename(path) + ":" + str(table["line"])})
    return result


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_part(archive: zipfile.ZipFile, name: str, ctx: _Context) -> bytes:
    ctx.checkpoint()
    normalized = posixpath.normpath(name).lstrip("/")
    try:
        info = archive.getinfo(normalized)
    except KeyError:
        raise ValueError("missing OOXML part: " + normalized)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("OOXML part exceeds the XML size limit: " + normalized)
    data = archive.read(info)
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data[:4096].upper():
        raise ValueError("OOXML XML declarations with custom entities are not accepted")
    return data


def _column_number(reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", reference or "")
    if not match:
        return 0
    number = 0
    for char in match.group(1).upper():
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _xlsx_text(cell: ET.Element, shared: Sequence[str]) -> str:
    cell_type = _text(cell.attrib.get("t"))
    if cell_type == "inlineStr":
        inline = next((child for child in cell if _local_name(child.tag) == "is"), None)
        if inline is not None:
            return "".join(node.text or "" for node in inline.iter() if _local_name(node.tag) == "t")
    value_node = next((child for child in cell if _local_name(child.tag) == "v"), None)
    if value_node is None or value_node.text is None:
        direct_text = [node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"]
        return "".join(direct_text) if direct_text else ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            raise ValueError("shared-string index is invalid: " + raw[:40])
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE" if raw == "0" else raw
    return raw


def _xlsx_sheets(path: str, ctx: _Context) -> List[dict]:
    tables = []
    with zipfile.ZipFile(path) as archive:
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > MAX_XLSX_TOTAL_BYTES:
            raise ValueError("XLSX uncompressed content exceeds the size limit")
        shared: List[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(_xml_part(archive, "xl/sharedStrings.xml", ctx))
            for item in root:
                if _local_name(item.tag) == "si":
                    value = "".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t")
                    if len(value) > MAX_CELL_TEXT:
                        raise ValueError("shared string exceeds the cell text limit")
                    shared.append(value)
        workbook = ET.fromstring(_xml_part(archive, "xl/workbook.xml", ctx))
        rels = ET.fromstring(_xml_part(archive, "xl/_rels/workbook.xml.rels", ctx))
        targets = {}
        for rel in rels:
            rid = _text(rel.attrib.get("Id"))
            target = _text(rel.attrib.get("Target"))
            if rid and target:
                targets[rid] = (posixpath.normpath(target.lstrip("/")) if target.startswith("/")
                                else posixpath.normpath(posixpath.join("xl", target)))
        sheet_nodes = [node for node in workbook.iter() if _local_name(node.tag) == "sheet"]
        for sheet in sheet_nodes:
            ctx.checkpoint()
            name = _text(sheet.attrib.get("name")) or MISSING
            rid = ""
            for key, value in sheet.attrib.items():
                if _local_name(key) == "id":
                    rid = value
                    break
            target = targets.get(rid)
            if not target:
                ctx.warn("xlsx_sheet_without_target", "worksheet relationship is missing",
                         path, "xlsx_parse", sheet=name)
                continue
            root = ET.fromstring(_xml_part(archive, target, ctx))
            parsed_rows: List[List[str]] = []
            truncated = False
            for row_node in (node for node in root.iter() if _local_name(node.tag) == "row"):
                ctx.checkpoint()
                values: List[str] = []
                for cell in (node for node in row_node if _local_name(node.tag) == "c"):
                    reference = _text(cell.attrib.get("r"))
                    column = _column_number(reference) if reference else len(values) + 1
                    if column <= 0:
                        column = len(values) + 1
                    if column > 16384:
                        truncated = True
                        continue
                    text = _xlsx_text(cell, shared)
                    if len(text) > MAX_CELL_TEXT:
                        raise ValueError("XLSX cell exceeds the text size limit")
                    if column - 1 > len(values):
                        values.extend([""] * (column - 1 - len(values)))
                    if column - 1 == len(values):
                        values.append(text)
                    else:
                        values[column - 1] = text
                parsed_rows.append(values)
                if len(parsed_rows) >= MAX_TABLE_ROWS:
                    truncated = True
                    break
            if not parsed_rows and not any(_local_name(node.tag) == "sheetData" and len(node) for node in root.iter()):
                parsed_rows = []
            notes = ["XLSX values are stored cell text/cached values; formulas and display formats were not evaluated"]
            if truncated:
                notes.append("worksheet row/column limit reached; exported cells are a partial extraction")
                ctx.warn("xlsx_sheet_limit", "worksheet extraction reached a row/column limit", path, sheet=name)
            tables.append({"sheet": name, "rows": parsed_rows, "hints": [name], "notes": notes,
                           "source_locator": target})
    return tables


def _parse_xlsx(entry: dict, ctx: _Context) -> List[dict]:
    result = []
    for sheet in _xlsx_sheets(entry["path"], ctx):
        sheet["entry"] = entry
        sheet["hints"] = _dedupe(_metadata_hints(entry) + sheet["hints"])
        result.append(sheet)
    return result


def _extract_tables(entries: Sequence[dict], ctx: _Context) -> List[dict]:
    tables: List[dict] = []
    for entry in sorted(entries, key=lambda item: item["source_id"]):
        ext = os.path.splitext(entry["path"])[1].lower()
        if ext not in TABLE_SUFFIXES:
            continue
        ctx.checkpoint()
        try:
            if not os.path.isfile(entry["path"]):
                raise OSError("table source file is not available")
            if ext in {".csv", ".tsv"}:
                parsed = _parse_delimited(entry, ctx)
            elif ext == ".xlsx":
                parsed = _parse_xlsx(entry, ctx)
            else:
                parsed = _parse_markdown(entry, ctx)
            if ext in {".csv", ".tsv", ".xlsx"}:
                parsed = [table for table in parsed if table["rows"]]
            for table in parsed:
                table["entry"]["table_count"] += 1
                tables.append(table)
        except _BudgetExceeded:
            raise
        except Exception as exc:
            ctx.warn("table_parse_failed", str(exc), entry["path"], "table_parse", format=ext)
            entry["notes"].append("table parsing failed: " + str(exc)[:180])
    return tables


def _caption_parts(text: str) -> Tuple[str, str, str]:
    normalized = normalize_text(text)
    match = TABLE_RE.match(normalized)
    if not match:
        return "", "", normalized
    label = match.group(1).upper().replace("-", ".")
    body = normalized[match.end():].lstrip(" .．。:：、-")
    return normalized, label, body


def _captions(entries: Sequence[dict], flat: Sequence[dict]) -> List[dict]:
    result = []
    pdf_entries = [entry for entry in entries if _is_pdf(entry)]
    multi = len(pdf_entries) > 1
    flat_by_source: Dict[str, set] = {}
    for block in flat:
        sid = _text(block.get("source_id"))
        if sid:
            flat_by_source.setdefault(sid, set()).add(_block_locator(block))
    for entry in pdf_entries:
        doc = entry.get("doc") or {}
        original_sid = _text(doc.get("source_id")) or entry["source_id"]
        for block in entry.get("blocks", []):
            if block.get("type") in {"reference", "furniture"}:
                continue
            text = block.get("text", block.get("raw", ""))
            if not isinstance(text, str):
                continue
            full, label, body = _caption_parts(text)
            if not label:
                continue
            # Body prose beginning with a table number is not itself a caption.
            # Untyped blocks are accepted because third-party indexers may omit
            # the type while preserving a genuine caption as a standalone block.
            if block.get("type") not in {"caption", "table_caption", None, ""}:
                continue
            locator = _block_locator(block)
            if not locator:
                continue
            known = flat_by_source.get(original_sid, set())
            if locator not in known and multi and not locator.startswith(original_sid + ":"):
                locator = original_sid + ":" + locator
            result.append({
                "source_id": entry["source_id"], "source_file": entry["path"],
                "locator": locator, "verbatim": text,
                "title": normalize_text(text), "full": _comparison_key(full),
                "label": label, "body": _comparison_key(body),
            })
    return result


def _caption_lookup(captions: Sequence[dict]) -> Dict[str, Dict[str, List[int]]]:
    lookup: Dict[str, Dict[str, List[int]]] = {"full": {}, "body": {}, "label": {}}
    for position, caption in enumerate(captions):
        for kind in lookup:
            key = caption[kind]
            if key:
                lookup[kind].setdefault(key, []).append(position)
    return lookup


def _lookup_candidates(keys: Iterable[str], index: Dict[str, List[int]],
                       captions: Sequence[dict]) -> List[dict]:
    positions = set()
    for key in keys:
        positions.update(index.get(key, []))
    return [captions[position] for position in sorted(positions)]


def _base_table_map(table: dict) -> dict:
    return {
        "title": MISSING,
        "source_file": table["entry"]["path"],
        "sheet": table.get("sheet") or MISSING,
        "csv_file": "",
        "caption_locator": MISSING,
        "caption_verbatim": MISSING,
        "mapping_method": "unmatched",
        "confidence": 0.0,
        "notes": "",
    }


def _map_table(table: dict, captions: Sequence[dict],
               lookup: Optional[Dict[str, Dict[str, List[int]]]] = None) -> dict:
    raw_hints = _dedupe(table.get("hints", []))
    parsed = [_caption_parts(hint) for hint in raw_hints]
    full_keys = {_comparison_key(parts[0]) for parts in parsed if parts[0]}
    body_keys = {_comparison_key(parts[2]) for parts in parsed
                 if parts[2] and len(_comparison_key(parts[2])) >= 4}
    labels = {parts[1] for parts in parsed if parts[1]}
    # Sheet names and unlabelled caption/title metadata can also be exact title bodies.
    for hint in raw_hints:
        key = _comparison_key(hint)
        if key and len(key) >= 4:
            body_keys.add(key)
    lookup = lookup if lookup is not None else _caption_lookup(captions)
    candidates = _lookup_candidates(full_keys, lookup["full"], captions)
    method = "normalized_caption_exact"
    if not candidates:
        candidates = _lookup_candidates(body_keys, lookup["body"], captions)
        method = "normalized_caption_body_exact"
    if not candidates and labels:
        candidates = _lookup_candidates(labels, lookup["label"], captions)
        method = "table_label_unique"
    base = _base_table_map(table)
    notes = list(table.get("notes", []))
    if not captions:
        notes.append("no table caption was found in the indexed PDF text blocks")
    elif not candidates:
        notes.append("no supplied caption/title/sheet hint normalized uniquely to an indexed PDF table caption")
    elif len(candidates) > 1:
        base["mapping_method"] = "ambiguous"
        base["confidence"] = 0.0
        notes.append("multiple PDF captions remain equally plausible: " +
                     "; ".join(c["locator"] for c in candidates[:8]))
    else:
        caption = candidates[0]
        base.update({
            "title": caption["title"],
            "caption_locator": caption["locator"],
            "caption_verbatim": caption["verbatim"],
            "mapping_method": method,
            "confidence": 0.99 if method == "normalized_caption_exact"
                          else 0.96 if method == "normalized_caption_body_exact" else 0.85,
        })
        notes.append("caption source: " + caption["source_id"] + " (" + caption["source_file"] + ")")
    base["notes"] = "; ".join(_compact(note, 450) for note in notes if _text(note))
    return base


def _source_file_rows(entries: Sequence[dict]) -> List[dict]:
    rows = []
    for entry in entries:
        notes = list(entry.get("notes", []))
        if entry.get("table_count"):
            notes.append("parsed fact tables: " + str(entry["table_count"]))
        rows.append({
            "source_id": entry["source_id"],
            "path": entry["path"],
            "title": entry["biblio"]["title"],
            "kind": entry["kind"],
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
            "notes": "; ".join(_compact(note, 350) for note in notes if _text(note)),
        })
    return rows


def _source_records(entries: Sequence[dict]) -> List[dict]:
    records = []
    for entry in entries:
        if not (_is_pdf(entry) or entry["kind"] == "primary_document"):
            continue
        biblio = entry["biblio"]
        record = {
            "source_id": entry["source_id"],
            "title": biblio["title"], "authors": biblio["authors"],
            "journal": biblio["journal"], "year": biblio["year"], "doi": biblio["doi"],
            "source_file": entry["path"],
            "title_locator": biblio["title_locator"],
            "authors_locator": biblio["authors_locator"],
            "journal_locator": biblio["journal_locator"],
            "year_locator": biblio["year_locator"],
            "doi_locator": biblio["doi_locator"],
            "notes": "bibliographic fields are validated against indexed text blocks; absent fields remain " + MISSING,
        }
        records.append(record)
    return records


def _write_csv(path: str, fields: Sequence[str], rows: Sequence[dict]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, MISSING) for key in fields})


def _write_json(path: str, value: Any) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_rows(path: str, rows: Sequence[Sequence[Any]]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)


def _safe_write(ctx: _Context, written: List[str], path: str, writer: Any, *args: Any) -> bool:
    try:
        writer(path, *args)
        written.append(os.path.abspath(path))
        return True
    except Exception as exc:
        ctx.warn("output_write_failed", str(exc), path, "output_write")
        return False


def _citation_text(records: Sequence[dict]) -> str:
    if not records:
        return MISSING + ": no primary or supplementary PDF citation could be grounded in indexed text.\n"
    lines = []
    for record in records:
        lines.append(
            "[{source_id}] Authors: {authors}. Title: {title}. Journal: {journal}. "
            "Year: {year}. DOI: {doi}. Source file: {source_file}. "
            "Title locator: {title_locator}.".format(**record)
        )
    return "\n".join(lines) + "\n"


def _build_source_bundle(case: dict, index: dict, outdir: str,
                         initial_warnings: Optional[List[dict]] = None) -> dict:
    ctx = _Context(initial_warnings)
    outdir = os.path.abspath(os.path.expanduser(_text(outdir) or "."))
    entries: List[dict] = []
    flat: List[dict] = []
    tables: List[dict] = []
    table_rows: List[dict] = []
    source_rows: List[dict] = []
    biblio_rows: List[dict] = []
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as exc:
        ctx.warn("outdir_create_failed", str(exc), outdir, "output_write")
    if not isinstance(case, dict):
        ctx.warn("invalid_case", "case must be an object; using an empty case")
        case = {}
    if not isinstance(index, dict):
        ctx.warn("invalid_index", "index must be an object; using an empty index")
        index = {}
    index_warnings = index.get("warnings", [])
    if isinstance(index_warnings, list):
        for message in index_warnings:
            ctx.warn("index_warning", _compact(message, 800), operation="index")
    try:
        entries, flat = _inventory(case, index, outdir, ctx)
    except _BudgetExceeded as exc:
        ctx.warn("time_budget", str(exc), operation="inventory")
    except Exception as exc:
        ctx.warn("inventory_failed", str(exc), operation="inventory")
    try:
        tables = _extract_tables(entries, ctx)
    except _BudgetExceeded as exc:
        ctx.warn("time_budget", str(exc), operation="table_extraction")
    except Exception as exc:
        ctx.warn("table_discovery_failed", str(exc), operation="table_extraction")
    try:
        captions = _captions(entries, flat)
    except Exception as exc:
        captions = []
        ctx.warn("caption_discovery_failed", str(exc), operation="caption_mapping")
    written: List[str] = []
    caption_lookup = _caption_lookup(captions)
    mapping_disabled = False
    for position, table in enumerate(tables, start=1):
        try:
            ctx.checkpoint()
        except _BudgetExceeded as exc:
            if not mapping_disabled:
                ctx.warn("time_budget", str(exc), operation="caption_mapping")
            mapping_disabled = True
        if mapping_disabled:
            mapped = _base_table_map(table)
            mapped["notes"] = "caption mapping was not completed within the internal time budget"
        else:
            try:
                mapped = _map_table(table, captions, caption_lookup)
            except Exception as exc:
                ctx.warn("table_mapping_failed", str(exc), table["entry"]["path"], "caption_mapping")
                mapped = _base_table_map(table)
                mapped["notes"] = "caption mapping failed: " + str(exc)[:180]
        mapped["table_id"] = "table_%03d" % position
        relative = os.path.join("tables", mapped["table_id"] + ".csv")
        destination = os.path.join(outdir, relative)
        if _safe_write(ctx, written, destination, _write_rows, table.get("rows", [])):
            mapped["csv_file"] = destination
        else:
            mapped["csv_file"] = "not_written"
            mapped["notes"] = (mapped["notes"] + "; " if mapped["notes"] else "") + "CSV export failed"
        table_rows.append({key: mapped[key] for key in TABLE_FIELDS})
    source_rows = _source_file_rows(entries)
    biblio_rows = _source_records(entries)

    source_json = {
        "schema": "source_files/1", "files": source_rows,
        "count": len(source_rows),
    }
    if not source_rows:
        source_json.update({"status": MISSING, "reason": "no readable or declared material file was discovered"})
    table_json: Dict[str, Any] = {"schema": "source_table_map/1", "tables": table_rows,
                                  "count": len(table_rows)}
    if not table_rows:
        table_json.update({
            "status": MISSING,
            "reason": "no fact table was discovered and parsed from CSV, TSV, XLSX, or Markdown materials",
        })
    elif any(row["mapping_method"] != "normalized_caption_exact" and
             row["mapping_method"] != "normalized_caption_body_exact" and
             row["mapping_method"] != "table_label_unique" for row in table_rows):
        table_json.update({"status": "partial",
                           "reason": "one or more exported tables has no unique PDF caption match"})
    else:
        table_json["status"] = "complete"
    if not biblio_rows:
        sources_json: Dict[str, Any] = {
            "schema": "sources/1", "sources": [], "count": 0,
            "status": MISSING,
            "reason": "no primary or supplementary PDF bibliographic record was available in the index",
        }
    else:
        sources_json = {"schema": "sources/1", "sources": biblio_rows, "count": len(biblio_rows)}

    _safe_write(ctx, written, os.path.join(outdir, "source_files.json"), _write_json, source_json)
    _safe_write(ctx, written, os.path.join(outdir, "source_files.csv"), _write_csv, SOURCE_FIELDS, source_rows)
    _safe_write(ctx, written, os.path.join(outdir, "source_table_map.json"), _write_json, table_json)
    _safe_write(ctx, written, os.path.join(outdir, "source_table_map.csv"), _write_csv, TABLE_FIELDS, table_rows)
    _safe_write(ctx, written, os.path.join(outdir, "sources.json"), _write_json, sources_json)
    _safe_write(ctx, written, os.path.join(outdir, "sources.csv"), _write_csv, BIBLIO_FIELDS, biblio_rows)
    _safe_write(ctx, written, os.path.join(outdir, "citations.txt"),
                lambda path, text: _write_text(path, text), _citation_text(biblio_rows))

    counts = {
        "source_files": len(source_rows),
        "primary_documents": sum(1 for row in source_rows if row["kind"] == "primary_document"),
        "fact_tables": len(table_rows),
        "exported_csv": sum(1 for row in table_rows if row["csv_file"] not in {"", "not_written"}),
        "matched_tables": sum(1 for row in table_rows if row["mapping_method"] not in {"unmatched", "ambiguous"}),
        "ambiguous_tables": sum(1 for row in table_rows if row["mapping_method"] == "ambiguous"),
        "unmatched_tables": sum(1 for row in table_rows if row["mapping_method"] == "unmatched"),
        "sources": len(biblio_rows),
        "citations": len(biblio_rows),
        "written_files": len(written),
        "warnings": len(ctx.warnings),
    }
    return {
        "schema": "source_bundle_report/1",
        "outdir": outdir,
        "counts": counts,
        "warnings": ctx.warnings,
        "written_files": written,
        "elapsed_seconds": round(max(0.0, time.monotonic() - ctx.started), 6),
    }


def _write_text(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def build_source_bundle(case: dict, index: dict, outdir: str) -> dict:
    """Build all source artifacts without network access or third-party imports.

    Parsing and output failures are represented by structured ``warnings``;
    successfully written artifacts are listed in ``written_files``.
    """
    return _build_source_bundle(case, index, outdir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline source inventory and fact-table mapper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a source bundle")
    build.add_argument("--case", required=True, help="case JSON object")
    build.add_argument("--index", required=True, help="docindex JSON object")
    build.add_argument("--outdir", required=True, help="output directory")
    args = parser.parse_args()
    if args.command != "build":
        parser.error("unsupported command")
    initial: List[dict] = []
    case = _load_json(args.case, "case", initial)
    index = _load_json(args.index, "index", initial)
    try:
        report = _build_source_bundle(case, index, args.outdir, initial)
    except Exception as exc:  # last-resort CLI boundary; normal parsing is isolated above
        report = {
            "schema": "source_bundle_report/1",
            "outdir": os.path.abspath(args.outdir),
            "counts": {"warnings": len(initial) + 1},
            "warnings": initial + [{"code": "bundle_failed", "message": str(exc)[:800]}],
            "written_files": [],
            "elapsed_seconds": 0.0,
        }
        print(json.dumps(report, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
