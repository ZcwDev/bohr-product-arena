#!/usr/bin/env python3
"""Self-check validator for evidence-bound writing skill.

Validates result.json against the output contract and performs integrity checks.

Usage::

    python3 selfcheck.py --result /app/output/result.json [--contract /input/case.json]

Exit codes:
    0 = all checks passed
    1 = contract validation failed
    2 = runtime error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    """Load JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_contract_fields(result: dict, contract: dict) -> list[dict]:
    """Check that all output_contract fields are present in result."""
    issues: list[dict] = []
    contract_fields = contract.get("output_contract", {})
    if not isinstance(contract_fields, dict):
        return issues
    
    for field_name in contract_fields:
        if field_name not in result:
            issues.append({
                "severity": "error",
                "check": "contract_field_missing",
                "field": field_name,
                "message": f"Output contract requires field '{field_name}' but it is missing from result.json"
            })
    
    return issues


def check_case_id(result: dict, contract: dict) -> list[dict]:
    """Verify case_id matches."""
    issues: list[dict] = []
    result_case_id = result.get("case_id")
    contract_case_id = contract.get("case_id")
    
    if not result_case_id:
        issues.append({
            "severity": "error",
            "check": "case_id_missing",
            "message": "result.json missing case_id"
        })
    elif contract_case_id and result_case_id != contract_case_id:
        issues.append({
            "severity": "error",
            "check": "case_id_mismatch",
            "expected": contract_case_id,
            "actual": result_case_id,
            "message": f"case_id mismatch: expected '{contract_case_id}', got '{result_case_id}'"
        })
    
    return issues


def check_status_enum(result: dict, contract: dict) -> list[dict]:
    """Verify status is from allowed enum."""
    issues: list[dict] = []
    status = result.get("status")
    
    if not status:
        issues.append({
            "severity": "error",
            "check": "status_missing",
            "message": "result.json missing status field"
        })
        return issues
    
    contract_oc = contract.get("output_contract", {})
    status_values = contract_oc.get("status_values")
    
    if status_values and isinstance(status_values, list):
        if status not in status_values:
            issues.append({
                "severity": "error",
                "check": "status_invalid",
                "status": status,
                "allowed": status_values,
                "message": f"status '{status}' not in allowed values: {status_values}"
            })
    
    return issues


def check_evidence_map(result: dict) -> list[dict]:
    """Check evidence_map structure."""
    issues: list[dict] = []
    emap = result.get("evidence_map")
    
    if not emap:
        issues.append({
            "severity": "warning",
            "check": "evidence_map_empty",
            "message": "evidence_map is empty or missing"
        })
        return issues
    
    if not isinstance(emap, list):
        issues.append({
            "severity": "error",
            "check": "evidence_map_not_list",
            "message": "evidence_map must be a list"
        })
        return issues
    
    for i, item in enumerate(emap):
        if not isinstance(item, dict):
            issues.append({
                "severity": "error",
                "check": "evidence_map_item_not_dict",
                "index": i,
                "message": f"evidence_map[{i}] is not a dict"
            })
            continue
        
        required_keys = ["claim", "locator", "quote", "verdict"]
        for key in required_keys:
            if key not in item:
                issues.append({
                    "severity": "warning",
                    "check": "evidence_map_item_missing_key",
                    "index": i,
                    "key": key,
                    "message": f"evidence_map[{i}] missing required key '{key}'"
                })
    
    return issues


def check_deliverable(result: dict) -> list[dict]:
    """Check deliverable structure."""
    issues: list[dict] = []
    deliverable = result.get("deliverable")
    
    if not deliverable:
        issues.append({
            "severity": "error",
            "check": "deliverable_missing",
            "message": "result.json missing deliverable field"
        })
        return issues
    
    if not isinstance(deliverable, dict):
        issues.append({
            "severity": "error",
            "check": "deliverable_not_dict",
            "message": "deliverable must be a dict"
        })
        return issues
    
    path = deliverable.get("path")
    if not path:
        issues.append({
            "severity": "warning",
            "check": "deliverable_path_missing",
            "message": "deliverable.path is missing"
        })
    elif not Path(path).is_file():
        issues.append({
            "severity": "warning",
            "check": "deliverable_file_not_found",
            "path": path,
            "message": f"deliverable.path '{path}' does not exist"
        })
    
    return issues


def check_forbidden_patterns(result: dict) -> list[dict]:
    """Check for forbidden naming patterns in generated identifiers and paths."""
    issues: list[dict] = []
    forbidden_prefixes = ["SYNTH-", "SYN-", "EDGE-", "TEST-", "REV-"]
    forbidden_dirs = ["caseA", "caseB", "caseC", "output_edge", "output_missing"]
    
    # Only check generated claim_ids and evidence claim_ids, not source case_ids
    generated_ids: list[str] = []
    emap = result.get("evidence_map", [])
    if isinstance(emap, list):
        for item in emap:
            if isinstance(item, dict):
                cid = item.get("claim_id")
                if cid:
                    generated_ids.append(str(cid))
    
    for prefix in forbidden_prefixes:
        for gid in generated_ids:
            if gid.startswith(prefix):
                issues.append({
                    "severity": "error",
                    "check": "forbidden_prefix",
                    "prefix": prefix,
                    "id": gid,
                    "message": f"Generated claim_id '{gid}' uses forbidden prefix '{prefix}'"
                })
    
    # Check directory names only in path fields
    def collect_paths(value) -> list[str]:
        paths = []
        if isinstance(value, str) and ("/" in value or "\\" in value):
            paths.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                paths.extend(collect_paths(v))
        elif isinstance(value, list):
            for item in value:
                paths.extend(collect_paths(item))
        return paths
    
    all_paths = collect_paths(result)
    for dirname in forbidden_dirs:
        for p in all_paths:
            if f"/{dirname}/" in p or f"\\{dirname}\\" in p:
                issues.append({
                    "severity": "error",
                    "check": "forbidden_directory",
                    "dirname": dirname,
                    "path": p,
                    "message": f"Forbidden directory '{dirname}' found in path: {p}"
                })
    
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-check validator for evidence-bound writing skill")
    parser.add_argument("--result", required=True, help="Path to result.json")
    parser.add_argument("--contract", default=None, help="Path to case.json (input contract)")
    parser.add_argument("--output", default=None, help="Write validation report to this path")
    args = parser.parse_args()
    
    result_path = Path(args.result)
    if not result_path.is_file():
        print(f"ERROR: result file not found: {result_path}", file=sys.stderr)
        return 2
    
    try:
        result = load_json(result_path)
    except Exception as e:
        print(f"ERROR: failed to load result.json: {e}", file=sys.stderr)
        return 2
    
    contract = {}
    if args.contract:
        contract_path = Path(args.contract)
        if contract_path.is_file():
            try:
                contract = load_json(contract_path)
            except Exception as e:
                print(f"WARNING: failed to load contract: {e}", file=sys.stderr)
    
    issues: list[dict] = []
    issues.extend(check_contract_fields(result, contract))
    issues.extend(check_case_id(result, contract))
    issues.extend(check_status_enum(result, contract))
    issues.extend(check_evidence_map(result))
    issues.extend(check_deliverable(result))
    issues.extend(check_forbidden_patterns(result))
    
    report = {
        "result_path": str(result_path),
        "contract_path": str(args.contract) if args.contract else None,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "issues": issues,
        "error_count": sum(1 for i in issues if i.get("severity") == "error"),
        "warning_count": sum(1 for i in issues if i.get("severity") == "warning"),
        "passed": sum(1 for i in issues if i.get("severity") == "error") == 0
    }
    
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(json.dumps(report, ensure_ascii=False, indent=2))
    
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
