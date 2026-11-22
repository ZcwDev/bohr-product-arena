#!/usr/bin/env python3
"""Validate a case `result.json` against the task's published output contract.

The platform publishes, per challenge, an `output_contract` (see the task's
`environment/resources/example-input.json`). Three families occur:

  reading  : case_id, status, result, claims, uncertainties
  writing  : case_id, deliverable, preserved_facts, evidence_map, change_log
  artifact : case_id, status, editable_source, export_artifact, content_map,
             compliance_checks, exceptions

This checker is deliberately mechanical: it does not judge scientific quality.
It answers one question only - "would a reviewer looking for the contract
fields find them, with the right shape and honest enums?"

CLI:
  python3 contract.py check  result.json [--family reading|writing|artifact] [--contract path.json]
  python3 contract.py stub   --family reading -o result.json
  python3 contract.py merge  result.json part.json      # deep-merge a patch into result.json

Exit code 0 when the file satisfies the family contract, 1 otherwise.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

FAMILIES = {
    "reading": {
        "required": ["case_id", "status", "result", "claims", "uncertainties"],
        "enum": {"status": ["answered", "partial", "insufficient_evidence", "input_error"]},
        "list_of_obj": ["claims"],
        "claim_keys": ["kind", "evidence"],
    },
    "writing": {
        "required": ["case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"],
        "enum": {},
        "list_of_obj": ["change_log"],
        "claim_keys": [],
    },
    "artifact": {
        "required": ["case_id", "status", "editable_source", "export_artifact",
                     "content_map", "compliance_checks", "exceptions"],
        "enum": {"status": ["ok", "partial", "unable", "input_error"]},
        "list_of_obj": ["content_map", "compliance_checks"],
        "claim_keys": [],
    },
}

STATUS_ALIASES = {
    "ok": "answered", "success": "answered", "complete": "answered", "done": "answered",
    "completed": "answered", "partial_result": "partial", "unable": "insufficient_evidence",
    "refused": "insufficient_evidence", "not_found": "insufficient_evidence",
    "error": "input_error", "failed": "input_error", "unreadable": "input_error",
}

PLACEHOLDER = {"", "-", "n/a", "na", "none", "null", "tbd", "todo", "?", "unknown",
               "待补充", "未知", "无"}


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in PLACEHOLDER
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def check(result: dict, family: str | None = None, contract: dict | None = None) -> dict:
    problems: list[str] = []
    notes: list[str] = []
    if family is None:
        family = guess_family(result)
    spec = FAMILIES.get(family)
    if spec is None:
        return {"family": None, "problems": [f"unknown family {family!r}"], "notes": []}

    required = list(spec["required"])
    if contract:
        for key in contract:
            if key not in required:
                required.append(key)

    # An input_error carries no readings by definition: the material was unreachable or
    # unusable. Demanding non-empty result/claims there forces the agent to invent an
    # answer to satisfy the checker, which is the opposite of what the field is for.
    unreadable = result.get("status") == "input_error"
    optional_when_unreadable = {"result", "claims"}

    for key in required:
        if key not in result:
            problems.append(f"missing top-level field {key!r}")
        elif _is_empty(result[key]) and key not in ("exceptions", "uncertainties"):
            if unreadable and key in optional_when_unreadable:
                notes.append(f"{key!r} is empty because status is input_error")
                continue
            problems.append(f"field {key!r} is empty; an absent answer is not an answer")

    status = result.get("status")
    if status is not None:
        allowed = spec["enum"].get("status") or []
        if allowed and status not in allowed:
            problems.append(f"status {status!r} not one of {allowed}")
        if _is_empty(status):
            problems.append("status present but empty")

    for key in spec["list_of_obj"]:
        value = result.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            problems.append(f"{key!r} must be a list, got {type(value).__name__}")
            continue
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                problems.append(f"{key}[{i}] must be an object, got {type(item).__name__}")
                continue
            if key == "claims":
                for ck in spec["claim_keys"]:
                    if _is_empty(item.get(ck)):
                        problems.append(f"{key}[{i}] missing {ck!r}")

    if not _is_empty(result.get("case_id")) and result.get("case_id") == "EXAMPLE-NOT-A-TEST-CASE":
        # legitimate when the run really is on the published interface example, which the task
        # itself ships with that id; only flag it when nothing says so.
        declares_example = bool(result.get("example_run") or result.get("deliverable_kind")
                                or "example" in json.dumps(result.get("limitations") or [], ensure_ascii=False).lower())
        if not declares_example:
            problems.append("case_id is the published example id but nothing in the result says the run "
                            "was on the example; add example_run=true or copy the real case_id verbatim")

    if status in ("partial", "insufficient_evidence", "unable", "refused"):
        limit = result.get("limitations") or result.get("uncertainties")
        if _is_empty(limit):
            notes.append("non-ok status without limitations/uncertainties: state what is missing "
                         "and where a human should take over")

    if result.get("status") == "answered":
        claims = result.get("claims")
        if isinstance(claims, list) and claims and all(_is_empty(c.get("evidence")) for c in claims
                                                       if isinstance(c, dict)):
            problems.append("status=answered but no claim carries evidence")

    return {"family": family, "required": required, "problems": problems, "notes": notes}


def guess_family(result: dict) -> str:
    keys = set(result)
    if {"deliverable", "preserved_facts"} & keys:
        return "writing"
    if {"editable_source", "export_artifact"} & keys:
        return "artifact"
    return "reading"


def normalize(result: dict) -> dict:
    """Repair only mechanical defects; never invent content."""
    out = copy.deepcopy(result)
    status = out.get("status")
    if isinstance(status, str):
        key = status.strip().lower()
        if key in STATUS_ALIASES:
            if out.get("family") == "artifact" or "editable_source" in out:
                target = {"answered": "ok", "insufficient_evidence": "unable",
                          "input_error": "input_error", "partial": "partial"}.get(
                              STATUS_ALIASES[key], "ok")
            else:
                target = STATUS_ALIASES[key]
            out["status"] = target
    for key in ("claims", "change_log", "content_map", "compliance_checks"):
        if isinstance(out.get(key), dict):
            out[key] = [{"key": k, "value": v} for k, v in out[key].items()]
    return out


def stub(family: str) -> dict:
    base = {"case_id": "", "status": "partial", "limitations": []}
    if family == "reading":
        base.update({"result": {}, "claims": [], "uncertainties": []})
    elif family == "writing":
        base = {"case_id": "", "deliverable": {}, "preserved_facts": [],
                "evidence_map": [], "change_log": [],
                "status": "partial", "limitations": []}
    else:
        base.update({"editable_source": {}, "export_artifact": {}, "content_map": [],
                     "compliance_checks": [], "exceptions": []})
    return base


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a case result.json against its output contract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("result")
    c.add_argument("--family", default=None)
    c.add_argument("--contract", default=None, help="JSON file holding the published output_contract")
    c.add_argument("--normalize", action="store_true", help="rewrite safe mechanical fixes in place")
    s = sub.add_parser("stub")
    s.add_argument("--family", default="reading")
    s.add_argument("-o", "--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("result")
    m.add_argument("patch")
    a = ap.parse_args()

    if a.cmd == "stub":
        json.dump(stub(a.family), open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(a.out)
        return 0
    if a.cmd == "merge":
        base = json.load(open(a.result, encoding="utf-8"))
        patch = json.load(open(a.patch, encoding="utf-8"))
        json.dump(deep_merge(base, patch), open(a.result, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("merged", a.patch, "->", a.result)
        return 0

    raw = json.load(open(a.result, encoding="utf-8"))
    if a.normalize:
        fixed = normalize(raw)
        if fixed != raw:
            json.dump(fixed, open(a.result, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            raw = fixed
    contract = json.load(open(a.contract, encoding="utf-8")) if a.contract else None
    report = check(raw, a.family, contract)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
