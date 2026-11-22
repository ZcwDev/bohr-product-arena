#!/usr/bin/env python3
"""Validate a result.json against the writing-family contract.

Usage:
    python3 selfcheck.py --result PATH [--case PATH]

Exits 0 on pass, 1 on fail. Prints a human-readable report to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLACEHOLDER_SET = {"", "-", "n/a", "na", "none", "null", "tbd", "todo",
                   "?", "unknown", "待补充", "未知", "无"}

REQUIRED_FIELDS = ["case_id", "deliverable", "preserved_facts",
                   "evidence_map", "change_log"]


def is_placeholder(val) -> bool:
    if isinstance(val, str):
        return val.strip().lower() in PLACEHOLDER_SET
    return False


def check_result(result: dict, case: dict | None = None) -> list[str]:
    errors = []

    # 1. Required fields present and non-placeholder
    for f in REQUIRED_FIELDS:
        if f not in result:
            errors.append(f"MISSING required field: {f}")
            continue
        if is_placeholder(result.get(f)):
            errors.append(f"PLACEHOLDER in required field: {f} = {result[f]!r}")

    # 2. case_id matches input (if case provided)
    if case and result.get("case_id") != case.get("case_id"):
        errors.append(
            f"case_id mismatch: result={result.get('case_id')!r} "
            f"case={case.get('case_id')!r}"
        )

    # 3. deliverable structure (skip for input_error / insufficient_evidence)
    status = result.get("status", "")
    deliv = result.get("deliverable", {})
    if status not in ("input_error", "insufficient_evidence"):
        if isinstance(deliv, dict):
            for k in ["revised_manuscript", "response_letter"]:
                path = deliv.get(k)
                if not path:
                    errors.append(f"deliverable.{k}: missing path")
                elif isinstance(path, str) and not Path(path).exists():
                    errors.append(f"deliverable.{k}: file not found: {path}")
                elif isinstance(path, str) and Path(path).exists():
                    size = Path(path).stat().st_size
                    if size < 100:
                        errors.append(f"deliverable.{k}: file too small ({size} bytes)")

    # 4. preserved_facts is a list with location info
    pf = result.get("preserved_facts", [])
    if not isinstance(pf, list):
        errors.append(f"preserved_facts must be a list, got {type(pf).__name__}")
    elif len(pf) == 0 and result.get("status") != "input_error":
        errors.append("preserved_facts is empty (expected at least some facts)")
    else:
        for i, fact in enumerate(pf[:5]):
            if not isinstance(fact, dict):
                errors.append(f"preserved_facts[{i}]: not a dict")
                continue
            if "value" not in fact:
                errors.append(f"preserved_facts[{i}]: missing 'value'")
            if "section" not in fact and "line" not in fact:
                errors.append(f"preserved_facts[{i}]: no location (section/line)")

    # 5. evidence_map: each item has issue_id and location info
    em = result.get("evidence_map", [])
    if not isinstance(em, list):
        errors.append(f"evidence_map must be a list, got {type(em).__name__}")
    elif len(em) == 0 and result.get("status") not in ("input_error", "insufficient_evidence"):
        errors.append("evidence_map is empty")
    else:
        for i, item in enumerate(em[:5]):
            if not isinstance(item, dict):
                errors.append(f"evidence_map[{i}]: not a dict")
                continue
            if "issue_id" not in item:
                errors.append(f"evidence_map[{i}]: missing 'issue_id'")

    # 6. change_log: each item has action and reason
    cl = result.get("change_log", [])
    if not isinstance(cl, list):
        errors.append(f"change_log must be a list, got {type(cl).__name__}")
    elif len(cl) == 0 and result.get("status") not in ("input_error", "insufficient_evidence"):
        errors.append("change_log is empty")
    else:
        for i, item in enumerate(cl[:5]):
            if not isinstance(item, dict):
                errors.append(f"change_log[{i}]: not a dict")
                continue
            if "action" not in item:
                errors.append(f"change_log[{i}]: missing 'action'")
            if "reason" not in item:
                errors.append(f"change_log[{i}]: missing 'reason'")

    # 7. field_status for absent fields
    fs = result.get("field_status", {})
    if isinstance(fs, dict):
        for k, v in fs.items():
            if isinstance(v, dict) and v.get("status") in ("not_reported", "unable", "conflict"):
                if not v.get("reason"):
                    errors.append(f"field_status.{k}: declared absent but no reason given")

    # 8. case_id is a non-empty string (any prefix is acceptable from the platform)
    cid = result.get("case_id", "")
    if not cid or not isinstance(cid, str):
        errors.append("case_id is empty or not a string")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, help="Path to result.json")
    ap.add_argument("--case", default=None, help="Path to case.json (optional)")
    args = ap.parse_args()

    result_path = Path(args.result)
    if not result_path.exists():
        print(f"FAIL: result.json not found: {result_path}")
        return 1

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FAIL: cannot parse result.json: {e}")
        return 1

    case = None
    if args.case:
        case_path = Path(args.case)
        if case_path.exists():
            case = json.loads(case_path.read_text(encoding="utf-8"))

    errors = check_result(result, case)

    print(f"=== Selfcheck: {result_path} ===")
    print(f"case_id: {result.get('case_id')}")
    print(f"status: {result.get('status')}")
    print(f"preserved_facts: {len(result.get('preserved_facts', []))}")
    print(f"evidence_map: {len(result.get('evidence_map', []))}")
    print(f"change_log: {len(result.get('change_log', []))}")

    deliv = result.get("deliverable", {})
    if isinstance(deliv, dict):
        for k in ["revised_manuscript", "response_letter", "revised_docx",
                   "revised_pdf", "revised_png", "response_docx"]:
            p = deliv.get(k)
            if p and Path(p).exists():
                size = Path(p).stat().st_size
                print(f"  {k}: {p} ({size} bytes)")
            elif p:
                print(f"  {k}: {p} (NOT FOUND)")

    if errors:
        print(f"\nFAIL: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    else:
        print("\nPASS: all checks passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
