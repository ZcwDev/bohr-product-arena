#!/usr/bin/env python3
"""Assemble the final `result.json` from the working artifacts, contract-first.

Why this exists: a hand-written result.json drifts from the contract the moment
the organizer's `output_contract` differs from the one in the public example. This
script reads the contract keys out of `case_context.json` (captured from the real
`/input/case.json`) and makes sure EVERY key is present with a concrete value:

  * keys it knows (case_id/status/result/claims/uncertainties/limitations/...)
    are populated from the extraction;
  * keys it does not know are emitted as an explicit placeholder object
    {"not_reported": true, "reason": ...} and listed in `uncertainties`;
    they are never left as empty arrays or silently dropped.

It refuses to promote a run to `answered` when any requested field is missing or
unresolved, so the status cannot be inflated by accident.

CLI:
  python3 assemble.py build \
      --context work/case_context.json \
      --fields work/fields_filled.json \
      --out /app/output/result.json \
      [--material work/material.txt] [--blocks work/blocks.json] \
      [--change-log work/change_log.json] [--notes work/notes.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

KNOWN = {"case_id", "status", "result", "claims", "uncertainties", "limitations",
         "change_log", "source", "sources", "exceptions", "notes", "evidence",
         "answer", "findings", "deliverable_kind"}
EXAMPLE_CASE_ID = "EXAMPLE-NOT-A-TEST-CASE"


def _load(path, default=None):
    if not path:
        return default
    if not os.path.isfile(path):
        return default
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


def compute_status(ctx, rows) -> tuple[str, list[str]]:
    reasons = []
    if not ctx.get("case_id"):
        return "input_error", ["case_id was not present in the case payload; it is copied "
                              "verbatim or the run is reported as input_error, never guessed"]
    if not ctx.get("materials"):
        return "input_error", ["no readable material file was supplied for this case"]
    if (ctx.get("fields_origin") or "").startswith("unavailable"):
        return "partial", ["the organizer's target-fields list was not supplied, so field "
                          "coverage cannot be verified"]
    if not rows:
        return "insufficient_evidence", ["material read but no field row was produced"]

    statuses = [r.get("status") for r in rows]
    if any(s not in ("reported", "not_reported", "source_conflict", "extraction_failed")
           for s in statuses):
        return "partial", [f"field(s) left unfilled: "
                           f"{[r.get('field_id') for r in rows if r.get('status') not in ('reported','not_reported','source_conflict','extraction_failed')]}"]

    n_rep = statuses.count("reported")
    n_fail = statuses.count("extraction_failed")
    n_conf = statuses.count("source_conflict")
    n_abs = statuses.count("not_reported")

    if n_rep == 0 and n_fail == 0 and n_abs == len(rows):
        # everything genuinely absent: a faithful answer, but flag the read
        reasons.append(f"all {len(rows)} requested fields are not_reported in the supplied "
                       f"material; check that the PDF has a usable text layer before shipping")
        return "partial", reasons
    if n_fail:
        reasons.append(f"{n_fail} requested field(s) could not be extracted (extraction_failed)")
    if n_conf:
        reasons.append(f"{n_conf} field(s) have conflicting values in the material")
    if n_rep and (n_fail or n_conf or n_abs):
        reasons.append(f"{n_rep} of {len(rows)} requested fields reported; the rest are "
                       f"marked honestly rather than filled by default")
        return "partial", reasons
    if n_rep == len(rows):
        return "answered", reasons
    return "partial", reasons or ["not every requested field could be resolved"]


def build_claims(rows, source_id: str) -> list[dict]:
    claims = []
    for r in rows:
        status = r.get("status")
        loc = r.get("paragraph_id")
        quote = r.get("quote")
        if status == "reported":
            claims.append({
                "kind": "extraction",
                "field_id": r.get("field_id"),
                "claim": _claim_text(r),
                "evidence": {"locator": loc, "quote": quote},
                "source_id": source_id,
                "location": loc,
                "semantic_check": r.get("semantic_check"),
                "condition_bound": bool(r.get("condition")),
            })
        elif status == "not_reported":
            claims.append({
                "kind": "absence",
                "field_id": r.get("field_id"),
                "claim": (f"The material does not report a value for {r.get('field_id')}; "
                          f"recorded as not_reported, not as 0 or a default."),
                "evidence": {"locator": loc, "quote": quote},
                "source_id": source_id,
                "location": loc,
            })
        elif status == "source_conflict":
            claims.append({
                "kind": "conflict",
                "field_id": r.get("field_id"),
                "claim": (f"The material states more than one value for {r.get('field_id')}; "
                          f"both locators are given and neither value is preferred."),
                "evidence": {"locator": loc, "quote": quote,
                             "conflicting_locator": r.get("conflict_paragraph_id"),
                             "conflicting_quote": r.get("conflict_quote")},
                "source_id": source_id,
                "location": loc,
            })
        elif status == "extraction_failed":
            claims.append({
                "kind": "extraction_failure",
                "field_id": r.get("field_id"),
                "claim": f"Could not be read from the material: {r.get('failure')}",
                "evidence": {"locator": loc, "quote": quote},
                "source_id": source_id,
                "location": loc,
            })
    return claims


def _claim_text(r: dict) -> str:
    parts = []
    scalar = r.get("scalar")
    units = r.get("units")
    stat = r.get("statistic")
    head = f"{r.get('field_id')} = {scalar}"
    if units:
        head += f" {units}"
    if stat:
        head += f" ({stat})"
    parts.append(head)
    if r.get("condition"):
        parts.append(f"under {r['condition']}")
    if r.get("comparator"):
        parts.append(f"versus {r['comparator']}")
    return ", ".join([parts[0]]) + ("; " + "; ".join(parts[1:]) if len(parts) > 1 else "")


def build_uncertainties(rows, ctx, extra) -> list[dict]:
    out = []
    for r in rows:
        st = r.get("status")
        if st == "not_reported":
            out.append({"field_id": r.get("field_id"), "kind": "not_reported",
                        "item": (f"{r.get('field_id')} has no value in the material"
                                 + (f"; the absence is established by {r.get('paragraph_id')}"
                                    if r.get("paragraph_id") else
                                    "; no sentence states its absence either")),
                        "resolution": "not_reported"})
        elif st == "source_conflict":
            out.append({"field_id": r.get("field_id"), "kind": "source_conflict",
                        "item": f"{r.get('field_id')} is stated differently in two places",
                        "locators": [r.get("paragraph_id"), r.get("conflict_paragraph_id")],
                        "resolution": "both locators kept; no value selected"})
        elif st == "extraction_failed":
            out.append({"field_id": r.get("field_id"), "kind": "extraction_failed",
                        "item": r.get("failure"),
                        "locators": [r.get("paragraph_id")],
                        "resolution": "needs a human to read the page image"})
        elif st == "pending":
            out.append({"field_id": r.get("field_id"), "kind": "unfilled",
                        "item": "this requested field was never filled in",
                        "resolution": "human must complete it before the result is used"})
    for key in ("targets_shape",):
        if ctx.get(key):
            out.append({"kind": "input_shape", "item": f"target-fields payload shape: {ctx[key]}",
                        "resolution": "recorded so the same file shape can be re-parsed"})
    if (ctx.get("fields_origin") or "") != "organizer_target_fields":
        out.append({"kind": "coverage", "fields_origin": ctx.get("fields_origin"),
                    "item": ("field_ids did not come from the organizer's target-fields payload"
                             if ctx.get("field_ids") else "no requested fields were available"),
                    "resolution": "coverage against the real request is unverified"})
    for w in ctx.get("warnings") or []:
        out.append({"kind": "input_warning", "item": w, "resolution": "recorded verbatim"})
    out.extend(extra or [])
    return out


def build(ctx, rows, extra_notes, change_log) -> dict:
    contract = ctx.get("output_contract") or {}
    status, reasons = compute_status(ctx, rows)
    source_id = (ctx.get("source") or {}).get("title") if isinstance(ctx.get("source"), dict) else None
    if not source_id:
        mats = ctx.get("materials") or []
        source_id = os.path.basename(mats[0]["path"]) if mats else "unresolved_source"

    counts = {}
    for r in rows:
        counts[r.get("status") or "unfilled"] = counts.get(r.get("status") or "unfilled", 0) + 1

    result = {
        "material": {
            "source_id": source_id,
            "files": ctx.get("materials") or [],
            "organizer_interface_revision": ctx.get("organizer_interface_revision"),
        },
        "requested_fields": {
            "origin": ctx.get("fields_origin"),
            "count": ctx.get("field_count"),
            "field_ids": ctx.get("field_ids") or [],
            "targets_path": ctx.get("targets_path"),
        },
        "fields": rows,
        "field_status_counts": counts,
        "status_rationale": reasons,
    }

    claims = build_claims(rows, source_id)
    uncertainties = build_uncertainties(rows, ctx, extra_notes)

    limitations = []
    if status != "answered":
        limitations.extend(reasons)
        limitations.append("Fields are marked not_reported / source_conflict / extraction_failed "
                           "where the material does not settle them; a human should re-read the "
                           "cited pages before any of those values is used downstream.")
    if (ctx.get("fields_origin") or "") != "organizer_target_fields":
        limitations.append("The requested field list was not supplied by the organizer in this "
                           "run, so which fields SHOULD have been extracted is unverified.")
    for w in ctx.get("warnings") or []:
        limitations.append(w)

    out = {
        "case_id": ctx.get("case_id"),
        "status": status,
        "result": result,
        "claims": claims,
        "uncertainties": uncertainties,
        "limitations": limitations,
        "source": ctx.get("source"),
        "change_log": change_log or [],
    }

    # A run over the published interface example keeps its case_id verbatim, so the
    # result must say out loud that it is an example run - otherwise a reader cannot
    # tell it apart from a real case that happens to share that id.
    if out["case_id"] == EXAMPLE_CASE_ID:
        out["example_run"] = True
        limitations.append(
            f"case_id {EXAMPLE_CASE_ID!r} is the public interface example shipped with the task; "
            f"this run demonstrates the pipeline on SYNTHETIC material, not on an evaluation "
            f"case. The numbers here must not be treated as findings about any real paper."
        )

    # Contract-first sweep: any published key not handled above gets an explicit,
    # explained placeholder - never an empty array, never a silent omission.
    for key in contract:
        if key in out:
            if isinstance(out[key], (list, dict)) and len(out[key]) == 0 and key not in ("source",):
                out[key] = {"not_reported": True,
                            "reason": f"the contract field {key!r} has no content available from "
                                      f"this material; an enforced placeholder is used instead of "
                                      f"an empty value"}
                uncertainties.append({"kind": "contract_placeholder", "item": f"{key}: empty",
                                      "resolution": "explicit not_reported placeholder"})
            continue
        out[key] = {"not_reported": True,
                    "reason": f"contract field {key!r} has no producer in this Skill's pipeline; "
                              f"left as an explicit placeholder rather than guessed",
                    "contract_source": ctx.get("case_path")}
        uncertainties.append({"kind": "contract_placeholder",
                              "item": f"contract key {key!r} was requested but has no producer",
                              "resolution": "explicit not_reported placeholder; needs an "
                                            "extension to the pipeline"})
    for key, desc in contract.items():
        if isinstance(desc, str) and desc.strip():
            result.setdefault("contract_requirements", {})[key] = desc
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble result.json, contract-first")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--context", required=True)
    b.add_argument("--fields", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--notes", default=None)
    b.add_argument("--change-log", default=None)
    b.add_argument("--previous", default=None, help="existing result.json to diff into change_log")
    a = ap.parse_args()

    ctx = _load(a.context, {}) or {}
    rows = _rows(_load(a.fields, {}))
    extra = _load(a.notes, []) or []
    change_log = _load(a.change_log, []) or []

    if a.previous and os.path.isfile(a.previous):
        prev = _load(a.previous, {}) or {}
        prev_status = prev.get("status")
        prev_ids = {r.get("field_id") for r in _rows(prev.get("result", {}))} if isinstance(
            prev.get("result"), dict) else set()
        now_ids = {r.get("field_id") for r in rows}
        change_log.append({
            "step": "rebuild",
            "from_status": prev_status,
            "to_status": build(ctx, rows, extra, [])["status"],
            "fields_added": sorted(now_ids - prev_ids),
            "fields_removed": sorted(prev_ids - now_ids),
            "reason": "re-assembled from the current working artifacts",
        })

    payload = build(ctx, rows, extra, change_log)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    prev_path = a.out + ".prev"
    if os.path.isfile(a.out):
        with open(a.out, encoding="utf-8") as src, open(prev_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(json.dumps({"out": a.out, "status": payload["status"],
                      "fields": len(rows), "claims": len(payload["claims"]),
                      "uncertainties": len(payload["uncertainties"]),
                      "limitations": len(payload["limitations"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
