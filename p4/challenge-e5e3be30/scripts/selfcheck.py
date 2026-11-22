#!/usr/bin/env python3
"""Contract-based self-check for output artifacts.

Usage:
  python3 selfcheck.py --output DIR [--family reading]

Exit codes:
  0  all checks pass
  1  one or more checks failed

This script validates result.json against the output contract, verifies that
claimed files exist, and checks that verdicts use only allowed enum values.
Raw output is suitable for embedding in RUN.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ALLOWED_VERDICTS = {"supported", "partially_supported", "contradicted", "insufficient_evidence"}
ALLOWED_READING_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
ALLOWED_ARTIFACT_STATUS = {"ok", "partial", "unable", "input_error"}
ALLOWED_WRITING_STATUS = {"ok", "partial", "unable", "input_error"}
REQUIRED_FIELDS = {"case_id", "status"}


def check_result(result_path: str, family: str = "reading") -> dict:
    """Run all checks and return a structured report."""
    report = {
        "file": result_path,
        "exists": False,
        "parseable": False,
        "checks": [],
        "passed": 0,
        "failed": 0,
        "problems": [],
    }

    if not os.path.isfile(result_path):
        report["problems"].append(f"file not found: {result_path}")
        report["failed"] = 1
        return report
    report["exists"] = True
    report["checks"].append({"name": "file_exists", "ok": True})
    report["passed"] += 1

    try:
        result = json.load(open(result_path, encoding="utf-8"))
    except Exception as exc:
        report["problems"].append(f"JSON parse error: {str(exc)[:200]}")
        report["failed"] = 1
        return report
    report["parseable"] = True
    report["checks"].append({"name": "json_parseable", "ok": True})
    report["passed"] += 1

    # Required fields
    for field in REQUIRED_FIELDS:
        ok = field in result and result[field]
        report["checks"].append({"name": f"has_{field}", "ok": ok})
        if ok:
            report["passed"] += 1
        else:
            report["failed"] += 1
            report["problems"].append(f"missing required field: {field}")

    # Status enum
    status = result.get("status", "")
    if family == "reading":
        allowed = ALLOWED_READING_STATUS
    elif family == "artifact":
        allowed = ALLOWED_ARTIFACT_STATUS
    else:
        allowed = ALLOWED_WRITING_STATUS
    ok = status in allowed
    report["checks"].append({"name": "status_enum", "ok": ok, "value": status, "allowed": sorted(allowed)})
    if ok:
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["problems"].append(f"status '{status}' not in allowed set {sorted(allowed)}")

    # case_id not using banned prefixes
    case_id = str(result.get("case_id", ""))
    banned_prefixes = ("SYN-", "EDGE-", "TEST-", "REV-")
    banned_names = ("caseA", "caseB", "caseC", "output_edge", "output_missing", "portability", "selftest")
    prefix_ok = not any(case_id.startswith(p) for p in banned_prefixes)
    name_ok = not any(b in case_id for b in banned_names)
    ok = prefix_ok and name_ok
    report["checks"].append({"name": "case_id_naming", "ok": ok, "value": case_id})
    if ok:
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["problems"].append(f"case_id '{case_id}' uses banned prefix/name")

    # Claims validation (reading family)
    claims = result.get("claims", [])
    if isinstance(claims, list):
        report["checks"].append({"name": "claims_is_list", "ok": True, "count": len(claims)})
        report["passed"] += 1

        verdict_ok = 0
        verdict_bad = 0
        evidence_present = 0
        evidence_missing = 0

        for i, c in enumerate(claims):
            v = c.get("verdict", "")
            if v in ALLOWED_VERDICTS:
                verdict_ok += 1
            else:
                verdict_bad += 1
                report["problems"].append(f"claim[{i}] verdict '{v}' not allowed")
            ev = c.get("evidence", [])
            if ev and isinstance(ev, list):
                evidence_present += 1
            else:
                evidence_missing += 1

        report["checks"].append({
            "name": "claim_verdicts",
            "ok": verdict_bad == 0,
            "valid": verdict_ok,
            "invalid": verdict_bad,
        })
        report["checks"].append({
            "name": "claim_evidence",
            "ok": evidence_missing == 0,
            "with_evidence": evidence_present,
            "without_evidence": evidence_missing,
        })
        if verdict_bad == 0:
            report["passed"] += 1
        else:
            report["failed"] += 1
        if evidence_missing == 0:
            report["passed"] += 1
        else:
            report["failed"] += 1

    # Uncertainties field
    uncertainties = result.get("uncertainties", [])
    ok = isinstance(uncertainties, list)
    report["checks"].append({"name": "uncertainties_is_list", "ok": ok, "count": len(uncertainties) if ok else 0})
    if ok:
        report["passed"] += 1
    else:
        report["failed"] += 1

    # Non-empty result field
    result_text = result.get("result", "")
    ok = bool(result_text) and not isinstance(result_text, type(None))
    report["checks"].append({"name": "result_non_empty", "ok": ok})
    if ok:
        report["passed"] += 1
    else:
        report["failed"] += 1

    # Banned placeholder detection
    PLACEHOLDER_MARKERS = ("TODO", "PLACEHOLDER", "FIXME", "TBD", "xxx", "lorem ipsum")
    raw = json.dumps(result, ensure_ascii=False)
    found_placeholders = [m for m in PLACEHOLDER_MARKERS if m.lower() in raw.lower()]
    ok = not found_placeholders
    report["checks"].append({"name": "no_placeholders", "ok": ok, "found": found_placeholders})
    if ok:
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["problems"].append(f"placeholder markers found: {found_placeholders}")

    # Limitations present when non-answered
    if status not in ("answered", "ok"):
        lim = result.get("limitations", [])
        ok = isinstance(lim, list) and len(lim) > 0
        report["checks"].append({"name": "limitations_for_non_ok", "ok": ok})
        if ok:
            report["passed"] += 1
        else:
            report["failed"] += 1
            report["problems"].append("status is non-ok but limitations is empty")

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Contract-based output self-check")
    ap.add_argument("--output", default="/app/output",
                    help="Output directory containing result.json")
    ap.add_argument("--family", default="reading",
                    choices=("reading", "writing", "artifact"),
                    help="Output contract family")
    ap.add_argument("--result-file", default="result.json",
                    help="Result filename within output directory")
    a = ap.parse_args()

    result_path = os.path.join(os.path.abspath(a.output), a.result_file)
    report = check_result(result_path, a.family)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    total = report["passed"] + report["failed"]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SELFCHECK: {report['passed']}/{total} checks passed, "
          f"{report['failed']} failed", file=sys.stderr)
    if report["problems"]:
        print(f"PROBLEMS:", file=sys.stderr)
        for p in report["problems"]:
            print(f"  - {p}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
