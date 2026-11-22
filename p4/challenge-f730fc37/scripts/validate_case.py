#!/usr/bin/env python3
"""Structural validator for result.json against case contract.

Checks claims format, evidence locators, source inventory cross-check, and preview
file existence. Outputs JSON report. Exit 0 = pass, 1 = problems found, 2 = script error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_claims_format(claims):
    """Each claim must have kind, evidence{locator,quote}, source_id, location."""
    problems = []
    if not isinstance(claims, list):
        return [{"field": "claims", "problem": "claims must be a list", "found": type(claims).__name__}]
    
    for i, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            problems.append({"claim_index": i, "problem": "claim must be a dict", "found": type(claim).__name__})
            continue
        
        if "kind" not in claim:
            problems.append({"claim_index": i, "problem": "missing required field", "field": "kind"})
        
        evidence = claim.get("evidence")
        if not isinstance(evidence, dict):
            problems.append({"claim_index": i, "problem": "evidence must be a dict", "found": type(evidence).__name__})
        else:
            if "locator" not in evidence:
                problems.append({"claim_index": i, "field": "evidence.locator", "problem": "missing required field"})
            if "quote" not in evidence:
                problems.append({"claim_index": i, "field": "evidence.quote", "problem": "missing required field"})
        
        if "source_id" not in claim:
            problems.append({"claim_index": i, "problem": "missing required field", "field": "source_id"})
        if "location" not in claim:
            problems.append({"claim_index": i, "problem": "missing required field", "field": "location"})
    
    return problems


def validate_evidence_locators(claims):
    """Check that evidence locators are not empty strings."""
    problems = []
    for i, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            continue
        evidence = claim.get("evidence")
        if not isinstance(evidence, dict):
            continue
        locator = evidence.get("locator", "")
        if isinstance(locator, str) and not locator.strip():
            problems.append({"claim_index": i, "problem": "evidence.locator is empty", "found": repr(locator)})
    return problems


def validate_source_inventory(result_path):
    """Check that source_files.json exists alongside result.json."""
    result_dir = result_path.parent
    source_files = result_dir / "source_files.json"
    if not source_files.is_file():
        return [{"problem": "source inventory missing", "expected": str(source_files)}]
    return []


def validate_preview_files(result, outdir):
    """Check for preview PDF/PNG if deliverables were claimed."""
    problems = []
    artifact_manifest = result.get("artifact_manifest")
    if not isinstance(artifact_manifest, dict):
        return problems
    
    documents = artifact_manifest.get("documents", [])
    if not isinstance(documents, list):
        return problems
    
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("document_id", "unknown")
        pdf_path = doc.get("pdf")
        png_path = doc.get("png")
        
        if pdf_path and isinstance(pdf_path, str):
            full_pdf = outdir / pdf_path
            if not full_pdf.is_file():
                problems.append({"document_id": doc_id, "problem": "preview PDF missing", "expected": str(full_pdf)})
        
        if png_path and isinstance(png_path, str):
            full_png = outdir / png_path
            if not full_png.is_file():
                problems.append({"document_id": doc_id, "problem": "preview PNG missing", "expected": str(full_png)})
    
    return problems


def check(result_path, case_path, out_path, skill_dir):
    """Run all structural validations and write report."""
    problems = []
    notes = []
    
    try:
        result = load_json(result_path)
    except Exception as exc:
        problems.append({"problem": "cannot load result.json", "error": f"{type(exc).__name__}: {exc}"})
        report = {"exit_code": 1, "problems": problems, "notes": notes, "checks_performed": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1
    
    # Check required top-level fields
    required_fields = ["case_id", "status", "result", "claims", "uncertainties"]
    for field in required_fields:
        if field not in result:
            problems.append({"problem": "missing required top-level field", "field": field})
    
    # Validate claims format
    claims = result.get("claims", [])
    claims_problems = validate_claims_format(claims)
    problems.extend(claims_problems)
    notes.append(f"claims_format: {len(claims)} claims checked, {len(claims_problems)} problems")
    
    # Validate evidence locators
    locator_problems = validate_evidence_locators(claims)
    problems.extend(locator_problems)
    notes.append(f"evidence_locators: {len(locator_problems)} empty locators found")
    
    # Validate source inventory
    source_problems = validate_source_inventory(result_path)
    problems.extend(source_problems)
    notes.append(f"source_inventory: {'present' if not source_problems else 'missing'}")
    
    # Validate preview files
    outdir = result_path.parent
    preview_problems = validate_preview_files(result, outdir)
    problems.extend(preview_problems)
    notes.append(f"preview_files: {len(preview_problems)} missing previews")
    
    # Check status is valid
    valid_statuses = {"answered", "partial", "insufficient_evidence", "input_error"}
    status = result.get("status")
    if status not in valid_statuses:
        problems.append({"problem": "invalid status", "found": status, "valid": sorted(valid_statuses)})
    
    exit_code = 0 if not problems else 1
    report = {
        "exit_code": exit_code,
        "problems": problems,
        "notes": notes,
        "checks_performed": 4,
        "result_path": str(result_path),
        "case_path": str(case_path),
        "skill_dir": str(skill_dir),
    }
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main():
    parser = argparse.ArgumentParser(description="Structural validator for result.json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    check_parser = subparsers.add_parser("check", help="Run structural validation")
    check_parser.add_argument("--result", required=True, type=Path, help="Path to result.json")
    check_parser.add_argument("--case", required=True, type=Path, help="Path to case contract JSON")
    check_parser.add_argument("--out", required=True, type=Path, help="Path to write validation report")
    check_parser.add_argument("--skill-dir", required=True, type=Path, help="Path to skill directory")
    
    args = parser.parse_args()
    
    if args.command == "check":
        rc = check(args.result, args.case, args.out, args.skill_dir)
        sys.exit(rc)


if __name__ == "__main__":
    main()
