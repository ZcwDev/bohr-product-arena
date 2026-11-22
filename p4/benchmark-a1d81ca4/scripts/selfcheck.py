#!/usr/bin/env python3
"""Validate result.json against the output contract.

Usage:
    python selfcheck.py <result.json> [case.json]

Exit codes:
    0 = valid
    1 = validation errors found
    2 = file not found or parse error
"""
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ["status", "result", "claims", "uncertainties"]
VALID_STATUSES = ["answered", "partial", "insufficient_evidence", "input_error"]


def check(result: dict, case: dict = None) -> dict:
    """Validate result against contract. Returns {problems: [], notes: []}."""
    problems = []
    notes = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in result:
            problems.append(f"Missing required field: {field}")

    # Validate status
    if "status" in result:
        status = result["status"]
        if status not in VALID_STATUSES:
            problems.append(f"Invalid status '{status}', must be one of {VALID_STATUSES}")

    # Validate case_id matches if case provided
    if case and "case_id" in case:
        result_id = result.get("case_id")
        case_id = case["case_id"]
        if result_id != case_id:
            problems.append(f"case_id mismatch: result has '{result_id}', case has '{case_id}'")

    # Check claims structure
    if "claims" in result:
        claims = result["claims"]
        if not isinstance(claims, list):
            problems.append("claims must be a list")
        else:
            for i, claim in enumerate(claims):
                if not isinstance(claim, dict):
                    problems.append(f"claims[{i}] must be a dict")
                    continue
                for req in ["kind", "evidence", "source_id"]:
                    if req not in claim:
                        notes.append(f"claims[{i}] missing optional field: {req}")

    # Check uncertainties structure
    if "uncertainties" in result:
        uncertainties = result["uncertainties"]
        if not isinstance(uncertainties, list):
            problems.append("uncertainties must be a list")

    # Check result structure
    if "result" in result:
        res = result["result"]
        if isinstance(res, dict) and "answers" in res:
            answers = res["answers"]
            if not isinstance(answers, dict):
                problems.append("result.answers must be a dict")
            else:
                # Check each answer has status
                for field, answer in answers.items():
                    if not isinstance(answer, dict):
                        problems.append(f"result.answers.{field} must be a dict")
                        continue
                    if "status" not in answer:
                        notes.append(f"result.answers.{field} missing status")
                    elif answer["status"] not in VALID_STATUSES:
                        problems.append(f"result.answers.{field} has invalid status: {answer['status']}")

    return {"problems": problems, "notes": notes}


def main():
    if len(sys.argv) < 2:
        print("Usage: selfcheck.py <result.json> [case.json]", file=sys.stderr)
        return 2

    result_path = Path(sys.argv[1])
    if not result_path.exists():
        print(f"Error: {result_path} not found", file=sys.stderr)
        return 2

    try:
        result = json.loads(result_path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error parsing {result_path}: {e}", file=sys.stderr)
        return 2

    case = None
    if len(sys.argv) >= 3:
        case_path = Path(sys.argv[2])
        if case_path.exists():
            try:
                case = json.loads(case_path.read_text())
            except json.JSONDecodeError:
                pass

    report = check(result, case)

    if report["problems"]:
        print("VALIDATION FAILED:")
        for p in report["problems"]:
            print(f"  ✗ {p}")
    else:
        print("✓ Contract validation passed")

    if report["notes"]:
        print(f"\nNotes ({len(report['notes'])} items):")
        for n in report["notes"][:10]:
            print(f"  ℹ {n}")
        if len(report["notes"]) > 10:
            print(f"  ... and {len(report['notes']) - 10} more")

    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
