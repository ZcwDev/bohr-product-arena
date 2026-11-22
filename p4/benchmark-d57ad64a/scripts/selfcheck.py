#!/usr/bin/env python3
"""Self-check: validate skill outputs against the runtime contract.

Usage:
    python3 selfcheck.py --output-dir /app/output

Reads result.json (and optionally other artifacts) from --output-dir,
and writes a structured selfcheck.json report. Exits 0 on pass, 1 on warnings, 2 on errors.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Required top-level keys per the contract
REQUIRED_KEYS = [
    "case_id",
    "deliverable",
    "preserved_facts",
    "evidence_map",
    "change_log",
]

# Optional but expected keys
OPTIONAL_KEYS = [
    "status",
    "sources",
    "uncertainties",
    "limitations",
    "run_record",
]

# Required sub-keys inside deliverable
DELIVERABLE_KEYS = [
    "benchmark_items",
    "aggregates",
    "failure_taxonomy",
    "rater_agreement",
    "improvement_suggestions",
]

# Expected artifacts in output dir
EXPECTED_ARTIFACTS = [
    "result.json",
    "report.md",
    "per_item.tsv",
]


def check_file_exists(path: str, name: str) -> dict:
    """Check if an expected artifact file exists."""
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    return {
        "file": name,
        "exists": exists,
        "size_bytes": size,
        "pass": exists and size > 0,
    }


def check_result_json(result_path: str) -> dict:
    """Validate result.json structure and content."""
    checks = []

    if not os.path.isfile(result_path):
        return {"pass": False, "error": "result.json not found", "checks": []}

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"pass": False, "error": f"result.json is not valid JSON: {e}", "checks": []}

    # Check required keys
    for key in REQUIRED_KEYS:
        present = key in data
        checks.append({
            "check": f"required_key:{key}",
            "pass": present,
            "detail": f"{'present' if present else 'MISSING'}"
        })

    # Check optional keys
    for key in OPTIONAL_KEYS:
        present = key in data
        checks.append({
            "check": f"optional_key:{key}",
            "pass": present,
            "detail": f"{'present' if present else 'missing (warning)'}",
            "severity": "warning" if not present else "info"
        })

    # Check deliverable sub-keys
    deliverable = data.get("deliverable", {})
    for key in DELIVERABLE_KEYS:
        present = key in deliverable
        checks.append({
            "check": f"deliverable:{key}",
            "pass": present,
            "detail": f"{'present' if present else 'MISSING'}"
        })

    # Check benchmark_items
    items = deliverable.get("benchmark_items", [])
    n_items = len(items)
    checks.append({
        "check": "benchmark_items_count",
        "pass": n_items > 0,
        "detail": f"{n_items} items"
    })

    # Check each item has verdict
    items_with_verdict = sum(1 for it in items if it.get("verdict"))
    checks.append({
        "check": "all_items_have_verdict",
        "pass": items_with_verdict == n_items and n_items > 0,
        "detail": f"{items_with_verdict}/{n_items} items have verdict"
    })

    # Check each item has score
    items_with_score = sum(1 for it in items if isinstance(it.get("score"), (int, float)))
    checks.append({
        "check": "all_items_have_score",
        "pass": items_with_score == n_items and n_items > 0,
        "detail": f"{items_with_score}/{n_items} items have numeric score"
    })

    # Check failure taxonomy
    taxonomy = deliverable.get("failure_taxonomy", {})
    if isinstance(taxonomy, dict):
        checks.append({
            "check": "failure_taxonomy_is_dict",
            "pass": True,
            "detail": f"{len(taxonomy)} categories"
        })
    else:
        checks.append({
            "check": "failure_taxonomy_is_dict",
            "pass": False,
            "detail": f"unexpected type: {type(taxonomy).__name__}"
        })

    # Check rater_agreement
    agreement = deliverable.get("rater_agreement", {})
    # Agreement is computed if we have per-criterion data with kappa or percent_agreement
    has_agreement = False
    if isinstance(agreement, dict) and agreement:
        for criterion_key, criterion_data in agreement.items():
            if isinstance(criterion_data, dict):
                if "cohen_kappa" in criterion_data or "percent_agreement" in criterion_data:
                    has_agreement = True
                    break
        if agreement.get("agreement_computed"):
            has_agreement = True
    checks.append({
        "check": "rater_agreement_computed",
        "pass": has_agreement,
        "detail": f"agreement data present ({len(agreement)} criteria)" if agreement else "empty"
    })

    # Check improvement suggestions
    suggestions = deliverable.get("improvement_suggestions", [])
    checks.append({
        "check": "improvement_suggestions",
        "pass": isinstance(suggestions, list) and len(suggestions) > 0,
        "detail": f"{len(suggestions) if isinstance(suggestions, list) else 0} suggestions"
    })

    # Check status
    status = data.get("status", "")
    valid_statuses = ["answered", "reference_only", "input_error", "not_applicable"]
    checks.append({
        "check": "status_valid",
        "pass": status in valid_statuses or not status,
        "detail": f"status='{status}'"
    })

    # Check case_id is not empty
    case_id = data.get("case_id", "")
    checks.append({
        "check": "case_id_nonempty",
        "pass": bool(case_id),
        "detail": f"case_id='{case_id}'"
    })

    # Check preserved_facts is a list
    preserved = data.get("preserved_facts", [])
    checks.append({
        "check": "preserved_facts_is_list",
        "pass": isinstance(preserved, list),
        "detail": f"{len(preserved)} facts"
    })

    # Check evidence_map is a dict
    emap = data.get("evidence_map", {})
    checks.append({
        "check": "evidence_map_is_dict",
        "pass": isinstance(emap, dict),
        "detail": f"{len(emap)} entries"
    })

    all_pass = all(c.get("pass") for c in checks if c.get("severity") != "warning")
    return {"pass": all_pass, "checks": checks}


def check_report_md(report_path: str) -> dict:
    """Validate report.md content."""
    if not os.path.isfile(report_path):
        return {"pass": False, "error": "report.md not found", "checks": []}

    text = open(report_path, "r", encoding="utf-8").read()
    checks = []

    checks.append({
        "check": "report_has_title",
        "pass": "#" in text[:200],
        "detail": "title heading found" if "#" in text[:200] else "no title"
    })

    checks.append({
        "check": "report_has_items",
        "pass": "derived-" in text or "item" in text.lower(),
        "detail": "item references found" if ("derived-" in text or "item" in text.lower()) else "no items"
    })

    checks.append({
        "check": "report_has_scores",
        "pass": "/" in text and any(c.isdigit() for c in text),
        "detail": "scores found"
    })

    checks.append({
        "check": "report_substantial_length",
        "pass": len(text) > 500,
        "detail": f"{len(text)} chars"
    })

    return {"pass": all(c["pass"] for c in checks), "checks": checks}


def check_per_item_tsv(tsv_path: str) -> dict:
    """Validate per_item.tsv content."""
    if not os.path.isfile(tsv_path):
        return {"pass": False, "error": "per_item.tsv not found", "checks": []}

    lines = open(tsv_path, "r", encoding="utf-8").readlines()
    checks = []

    checks.append({
        "check": "tsv_has_header",
        "pass": len(lines) > 0 and "item_id" in lines[0].lower(),
        "detail": f"header: {lines[0].strip()[:80]}" if lines else "empty"
    })

    n_data_rows = len(lines) - 1
    checks.append({
        "check": "tsv_has_data_rows",
        "pass": n_data_rows > 0,
        "detail": f"{n_data_rows} data rows"
    })

    # Check each row has tabs
    valid_rows = sum(1 for line in lines[1:] if "\t" in line)
    checks.append({
        "check": "tsv_rows_have_tabs",
        "pass": valid_rows == n_data_rows,
        "detail": f"{valid_rows}/{n_data_rows} rows properly tab-delimited"
    })

    return {"pass": all(c["pass"] for c in checks), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description="Self-check: validate skill outputs")
    ap.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                    help="Output directory to validate")
    args = ap.parse_args()

    output_dir = args.output_dir
    if not os.path.isdir(output_dir):
        print(f"ERROR: output dir does not exist: {output_dir}", file=sys.stderr)
        return 2

    report = {
        "output_dir": output_dir,
        "checks": {},
        "overall_pass": True,
        "warnings": [],
        "errors": [],
    }

    # Check each artifact
    for artifact in EXPECTED_ARTIFACTS:
        path = os.path.join(output_dir, artifact)
        report["checks"][f"artifact:{artifact}"] = check_file_exists(path, artifact)

    # Detailed checks for result.json
    result_path = os.path.join(output_dir, "result.json")
    result_check = check_result_json(result_path)
    report["checks"]["result_json"] = result_check

    # Check report.md
    report_path = os.path.join(output_dir, "report.md")
    report_check = check_report_md(report_path)
    report["checks"]["report_md"] = report_check

    # Check per_item.tsv
    tsv_path = os.path.join(output_dir, "per_item.tsv")
    tsv_check = check_per_item_tsv(tsv_path)
    report["checks"]["per_item_tsv"] = tsv_check

    # Aggregate
    for name, check in report["checks"].items():
        if not check.get("pass"):
            if check.get("severity") == "warning":
                report["warnings"].append(f"{name}: {check.get('error') or check.get('detail', 'failed')}")
            else:
                report["errors"].append(f"{name}: {check.get('error') or check.get('detail', 'failed')}")

    report["overall_pass"] = len(report["errors"]) == 0

    # Also write summary-compatible format
    report["items"] = len(json.load(open(result_path)).get("deliverable", {}).get("benchmark_items", [])) if os.path.isfile(result_path) else 0
    report["with_candidates"] = report["items"]  # if items have candidates
    report["raters"] = 2 if report["checks"].get("result_json", {}).get("checks") and any(
        c.get("check") == "rater_agreement_computed" and c.get("pass")
        for c in report["checks"]["result_json"].get("checks", [])) else 0
    report["every_item_has_verdict"] = any(
        c.get("check") == "all_items_have_verdict" and c.get("pass")
        for c in report["checks"].get("result_json", {}).get("checks", []))
    report["taxonomy_covers_all_failures"] = any(
        c.get("check") == "failure_taxonomy_is_dict" and c.get("pass")
        for c in report["checks"].get("result_json", {}).get("checks", []))
    report["agreement_computed"] = any(
        c.get("check") == "rater_agreement_computed" and c.get("pass")
        for c in report["checks"].get("result_json", {}).get("checks", []))
    # Also check raw data: if rater_agreement has kappa, it's computed
    if not report["agreement_computed"] and os.path.isfile(result_path):
        try:
            _rj = json.load(open(result_path))
            _ra = _rj.get("deliverable", {}).get("rater_agreement", {})
            if _ra:
                report["agreement_computed"] = any(
                    isinstance(v, dict) and ("cohen_kappa" in v or "percent_agreement" in v)
                    for v in _ra.values())
                report["raters"] = 2 if report["agreement_computed"] else 0
        except Exception:
            pass
    report["output_contract_fields_present"] = report["checks"].get("result_json", {}).get("pass", False)

    # Write selfcheck.json
    sc_path = os.path.join(output_dir, "selfcheck.json")
    with open(sc_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Selfcheck written to {sc_path}")

    # Print summary
    status = "PASS" if report["overall_pass"] else "FAIL"
    print(f"[selfcheck] {status}: {len(report['errors'])} errors, {len(report['warnings'])} warnings")
    for e in report["errors"]:
        print(f"  ERROR: {e}")
    for w in report["warnings"]:
        print(f"  WARN: {w}")

    return 0 if report["overall_pass"] else (1 if report["warnings"] else 2)


if __name__ == "__main__":
    sys.exit(main())
