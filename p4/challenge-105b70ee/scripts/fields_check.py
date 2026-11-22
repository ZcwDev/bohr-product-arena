#!/usr/bin/env python3
"""Mechanical gate for the organizer's revision-v2 per-field contract.

The v2 interface requires, for EVERY requested field_id:

  scalar, units, statistic, condition, comparator, paragraph_id, quote, status

with status in reported | not_reported | source_conflict | extraction_failed.
Three rules that this checker enforces and a prose review would miss:

  * one row per requested field_id, no extras, no duplicates, no omissions;
  * `not_reported` must carry a NULL value - an absent field is not zero;
  * every `reported` row's quote must be found verbatim in the material actually
    read, at the paragraph_id it claims (a quote that exists elsewhere does not
    retroactively verify the row).

What it deliberately does NOT do: judge whether the sentence means what the row
says it means. That is `semantic_check`, which only a reader can fill in, and
which this checker only verifies is non-empty for `reported` rows.

CLI:
  python3 fields_check.py check work/fields_filled.json \
      --scaffold work/fields_scaffold.json \
      --blocks work/blocks.json \
      [--material work/material.txt] \
      [-o work/fields_check.json]

  python3 fields_check.py table work/fields_filled.json      # human-readable audit table

Exit 0 when there are no problems, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

FILLED_STATUS = ("reported", "not_reported", "source_conflict", "extraction_failed")
VALUE_KEYS = ("scalar", "units", "statistic", "condition", "comparator",
              "paragraph_id", "quote")
NULLISH = (None, "", [], {})


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("fields", "results", "target_fields", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return len(value) == 0 if isinstance(value, (list, dict)) else False


def build_index(blocks_path: str | None):
    """Return {locator: normalized_text} plus a normalized full-text blob."""
    if not blocks_path or not os.path.isfile(blocks_path):
        return {}, ""
    data = _load(blocks_path)
    blocks = data.get("blocks") if isinstance(data, dict) else data
    index = {}
    for b in blocks or []:
        loc = b.get("id") or b.get("locator")
        if loc:
            index[str(loc)] = _norm(b.get("text", ""))
    return index, _norm(" ".join(index.values()))


def _norm(text: str) -> str:
    from importlib import util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = util.spec_from_file_location("_evidence_for_norm", os.path.join(here, "evidence.py"))
    if spec and spec.loader:
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.normalize(text).lower()
    return re.sub(r"\s+", " ", text).strip().lower()


def check(filled, scaffold, blocks_index, blocks_blob, material_text) -> dict:
    problems: list[str] = []
    notes: list[str] = []
    rows = _rows(filled)
    if not rows:
        return {"problems": ["no field rows found: nothing was extracted"],
                "notes": [], "counts": {}}

    requested = []
    if scaffold:
        requested = [f.get("field_id") for f in _rows(scaffold)]
    origin = (scaffold or {}).get("fields_origin") if isinstance(scaffold, dict) else None

    got = [r.get("field_id") for r in rows]
    dupes = sorted({f for f in got if got.count(f) > 1})
    if dupes:
        problems.append(f"duplicate field_id rows: {dupes}")
    if requested:
        missing = [f for f in requested if f not in got]
        extra = [f for f in got if f not in requested]
        if missing:
            problems.append(f"requested field_id with no result row: {missing}")
        if extra:
            problems.append(f"result row for a field_id that was never requested: {extra}")
    else:
        notes.append("no scaffold supplied: field coverage against the request could not be "
                     "checked; coverage claims are unverified")

    counts = {s: 0 for s in FILLED_STATUS}
    pending = []
    for r in rows:
        fid = r.get("field_id") or "<no field_id>"
        status = r.get("status")
        if status == "pending":
            pending.append(fid)
            continue
        if status not in FILLED_STATUS:
            problems.append(f"{fid}: status {status!r} not one of {list(FILLED_STATUS)}")
            continue
        counts[status] += 1

        if status == "not_reported":
            leaked = [k for k in ("scalar", "units") if not _blank(r.get(k))]
            if leaked:
                problems.append(f"{fid}: status=not_reported but {leaked} carry a value; "
                                f"an absent field is not zero and not a default")
            if not _blank(r.get("paragraph_id")) and _blank(r.get("quote")):
                problems.append(f"{fid}: not_reported with a paragraph_id but no quote; cite the "
                                f"sentence that establishes the absence, or clear the locator")
            continue

        if status == "source_conflict":
            locs = []
            for key in ("paragraph_id", "conflict_paragraph_id"):
                v = r.get(key)
                if not _blank(v):
                    locs.extend(v if isinstance(v, list) else [v])
            if len(locs) < 2:
                problems.append(f"{fid}: source_conflict needs two locators "
                                f"(paragraph_id + conflict_paragraph_id), got {locs}")
            continue

        if status == "extraction_failed":
            if _blank(r.get("failure") or r.get("failure_point")):
                problems.append(f"{fid}: extraction_failed without a `failure` description; "
                                f"say what could not be read and where")
            counts["extraction_failed"] += 0
            continue

        # status == reported
        for key in VALUE_KEYS:
            if key not in r:
                problems.append(f"{fid}: reported row is missing key {key!r}")
        if _blank(r.get("scalar")):
            problems.append(f"{fid}: status=reported but scalar is empty; not_reported is the "
                            f"honest status when no value is stated")
        if _blank(r.get("paragraph_id")):
            problems.append(f"{fid}: status=reported but paragraph_id is empty")
        if _blank(r.get("semantic_check")):
            problems.append(f"{fid}: status=reported but semantic_check is empty; a literal "
                            f"quotation match does not establish that the sentence states "
                            f"this field")
        quote = r.get("quote")
        loc = r.get("paragraph_id")
        if not _blank(quote):
            q = _norm(quote)
            if isinstance(loc, str) and blocks_index:
                block = blocks_index.get(loc)
                if block is None:
                    problems.append(f"{fid}: paragraph_id {loc!r} is not a locator in blocks.json")
                elif q not in block:
                    if q in blocks_blob:
                        problems.append(f"{fid}: quote exists in the material but NOT at the "
                                        f"claimed locator {loc!r}; fix paragraph_id")
                    else:
                        problems.append(f"{fid}: quote not found verbatim in the material")
            elif not blocks_index and material_text:
                if q not in _norm(material_text):
                    problems.append(f"{fid}: quote not found verbatim in the supplied material")

    if pending:
        problems.append(f"{len(pending)} field(s) still pending (status not filled): {pending[:10]}")
    if counts.get("reported", 0) == 0 and not pending and counts.get("extraction_failed", 0) == 0:
        notes.append("no field reached status=reported: confirm the material really lacks every "
                     "requested value before shipping this as an extraction result")
    if origin and origin != "organizer_target_fields":
        notes.append(f"field_ids came from {origin}, not from the organizer's target-fields "
                     f"payload; coverage against the real request is unverified")
    return {"problems": problems, "notes": notes, "counts": counts,
            "n_rows": len(rows), "n_requested": len(requested), "fields_origin": origin}


def table(rows) -> str:
    header = ("field_id", "status", "scalar", "units", "statistic", "condition",
              "comparator", "locator")
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in _rows(rows):
        cells = [str(r.get("field_id") or "")] + [
            str(r.get(k) if r.get(k) is not None else "") for k in
            ("status", "scalar", "units", "statistic", "condition", "comparator")]
        cells.append(str(r.get("paragraph_id") or ""))
        clean = [c.replace("|", "\\|").replace("\n", " ")[:70] or "—" for c in cells]
        out.append("| " + " | ".join(clean) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a filled per-field extraction table")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("filled")
    c.add_argument("--scaffold", default=None)
    c.add_argument("--blocks", default=None)
    c.add_argument("--material", default=None)
    c.add_argument("-o", "--out", default=None)
    t = sub.add_parser("table")
    t.add_argument("filled")
    a = ap.parse_args()

    if a.cmd == "table":
        print(table(_load(a.filled)))
        return 0

    filled = _load(a.filled)
    scaffold = _load(a.scaffold) if a.scaffold and os.path.isfile(a.scaffold) else None
    index, blob = build_index(a.blocks)
    material_text = ""
    if a.material and os.path.isfile(a.material):
        with open(a.material, encoding="utf-8", errors="replace") as fh:
            material_text = fh.read()
    report = check(filled, scaffold, index, blob, material_text)
    payload = json.dumps(report, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    print(payload)
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
