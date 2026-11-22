#!/usr/bin/env python3
"""Validate that a skill output directory conforms to the writing-12 contract.

Usage:
  python3 skill/scripts/selfcheck.py --dir DIR [--family writing]

Exit code: 0 = clean, 1 = problems found.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path


WRITING_REQUIRED_KEYS = ["case_id", "status", "deliverable", "preserved_facts",
                         "evidence_map", "change_log"]
VALID_STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}


def check_result(result: dict, family: str = "writing") -> dict:
    """Check a result.json against the contract."""
    problems = []
    notes = []
    warnings = []

    # Required top-level keys
    for k in WRITING_REQUIRED_KEYS:
        if k not in result:
            problems.append(f"missing top-level key: {k}")

    # case_id must be non-empty
    cid = result.get("case_id", "")
    if not cid:
        problems.append("case_id is empty")
    elif cid == "UNKNOWN":
        warnings.append("case_id is 'UNKNOWN' — was input missing or malformed?")

    # status must be valid
    st = result.get("status", "")
    if st not in VALID_STATUSES:
        problems.append(f"invalid status '{st}', expected one of {sorted(VALID_STATUSES)}")

    # If insufficient_evidence, should have explanation
    if st == "insufficient_evidence":
        lim = result.get("limitations", [])
        if not lim:
            warnings.append("insufficient_evidence but no limitations explaining why")

    # deliverable checks
    deliv = result.get("deliverable", {})
    if isinstance(deliv, dict):
        if not deliv.get("path") and not deliv.get("description"):
            if st != "insufficient_evidence":
                problems.append("deliverable has no path and no description")
    elif not deliv:
        if st not in ("insufficient_evidence", "input_error"):
            problems.append("deliverable is empty for a non-insufficient status")

    # preserved_facts must be a list
    pf = result.get("preserved_facts", None)
    if pf is None:
        problems.append("preserved_facts is missing")
    elif not isinstance(pf, list):
        problems.append("preserved_facts is not a list")
    elif st not in ("insufficient_evidence", "input_error") and len(pf) == 0:
        warnings.append("preserved_facts is empty despite answered/partial status")

    # evidence_map must be a list
    em = result.get("evidence_map", None)
    if em is None:
        problems.append("evidence_map is missing")
    elif not isinstance(em, list):
        problems.append("evidence_map is not a list")

    # change_log must be a list
    cl = result.get("change_log", None)
    if cl is None:
        problems.append("change_log is missing")
    elif not isinstance(cl, list):
        problems.append("change_log is not a list")

    # Check for placeholder markers
    raw = json.dumps(result, ensure_ascii=False)
    for marker in ["TODO", "PLACEHOLDER", "{{", "FIXME", "TBD"]:
        if marker in raw:
            warnings.append(f"result contains placeholder marker: '{marker}'")

    # Fabrication risk: status=answered but no deliverable
    if st == "answered" and not deliv.get("path"):
        problems.append("status=answered but no deliverable path — possible fabrication")

    return {
        "ok": len(problems) == 0,
        "problems": problems,
        "notes": notes,
        "warnings": warnings,
        "case_id": cid,
        "status": st,
        "checks_run": len(WRITING_REQUIRED_KEYS) + 5
    }


def check_artifacts(result: dict, out_dir: str) -> dict:
    """Check that referenced artifacts actually exist."""
    issues = []
    deliv = result.get("deliverable", {})
    if isinstance(deliv, dict) and deliv.get("path"):
        p = os.path.join(out_dir, deliv["path"])
        if not os.path.isfile(p):
            issues.append(f"deliverable file missing: {deliv['path']}")
        else:
            sz = os.path.getsize(p)
            if sz < 100:
                issues.append(f"deliverable suspiciously small ({sz} bytes): {deliv['path']}")

    for key in ("preview_pdf", "preview_png"):
        pv = deliv.get(key) if isinstance(deliv, dict) else None
        if pv:
            # Could be absolute or relative
            if os.path.isabs(pv):
                full = pv
            else:
                full = os.path.join(out_dir, pv)
            if not os.path.isfile(full):
                issues.append(f"{key} missing: {pv}")

    return {"artifact_issues": issues, "ok": len(issues) == 0}


def main():
    ap = argparse.ArgumentParser(description="Selfcheck: validate skill output")
    ap.add_argument("--dir", default="out", help="Output directory to check")
    ap.add_argument("--result", default="result.json", help="Result file name")
    ap.add_argument("--family", default="writing", help="Contract family")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--check-artifacts", action="store_true", default=True,
                    help="Also check referenced artifacts exist")
    a = ap.parse_args()

    rpath = os.path.join(a.dir, a.result)
    if not os.path.isfile(rpath):
        report = {"ok": False, "error": f"result file not found: {rpath}"}
        if a.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"❌ {rpath} not found", file=sys.stderr)
        return 1

    result = json.loads(Path(rpath).read_text(encoding="utf-8"))
    cr = check_result(result, a.family)

    if a.check_artifacts:
        ar = check_artifacts(result, a.dir)
        cr["artifact_issues"] = ar["artifact_issues"]
        if not ar["ok"]:
            cr["ok"] = False

    if a.json:
        print(json.dumps(cr, indent=2, ensure_ascii=False))
    else:
        status_icon = "✅" if cr["ok"] else "❌"
        print(f"{status_icon} {rpath}")
        print(f"  case_id: {cr['case_id']}")
        print(f"  status: {cr['status']}")
        if cr["problems"]:
            for p in cr["problems"]:
                print(f"  ❌ problem: {p}")
        if cr.get("warnings"):
            for w in cr["warnings"]:
                print(f"  ⚠️  warning: {w}")
        if cr.get("artifact_issues"):
            for i in cr["artifact_issues"]:
                print(f"  📦 artifact: {i}")
        if cr["ok"] and not cr.get("warnings"):
            print("  All contract checks passed.")

    return 0 if cr["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
