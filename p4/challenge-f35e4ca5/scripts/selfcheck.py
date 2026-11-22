#!/usr/bin/env python3
"""Self-check: validate a result.json against the contract + audit rules.

Combines ``contract.py check`` and ``audit_delivery.py`` into a single
gate that the wrapper and documentation can call.

CLI:
    python3 selfcheck.py result.json [--family reading] [--base /output]

Exit 0 = pass, 1 = fail.  Always prints a JSON report to stdout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load_sibling(name: str) -> Any:
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_contract(result_path: Path, family: str) -> dict[str, Any]:
    contract_mod = load_sibling("contract")
    if contract_mod is None:
        return {"ok": False, "problems": ["contract.py not loadable"]}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "problems": [f"cannot read result: {exc}"]}
    report = contract_mod.check(result, family=family)
    return report


def check_audit(result_path: Path, base: Path) -> dict[str, Any]:
    audit_mod = load_sibling("audit_delivery")
    if audit_mod is None:
        return {"ok": False, "problems": ["audit_delivery.py not loadable"]}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "problems": [f"cannot read result: {exc}"]}
    problems: list[str] = []
    # Required fields
    for key in audit_mod.REQUIRED:
        if key not in result:
            problems.append(f"missing required field: {key}")
    # Status enum
    status = result.get("status")
    if status not in audit_mod.STATUSES:
        problems.append(f"status '{status}' not in {audit_mod.STATUSES}")
    # Forbidden markers
    text = json.dumps(result, ensure_ascii=False)
    for marker in audit_mod.MARKERS:
        if marker in text:
            problems.append(f"forbidden marker found: {marker}")
    # Empty collections deep scan
    empties = audit_mod.empty_collections(result)
    # Check previews if applicable
    preview_problems = audit_mod.check_previews(result, base)
    problems.extend(preview_problems)
    return {
        "ok": len(problems) == 0,
        "problems": problems,
        "empty_collections": empties[:20],
        "required_fields_present": all(k in result for k in audit_mod.REQUIRED),
        "status_valid": status in audit_mod.STATUSES,
    }


def run_selfcheck(result_path: Path, family: str, base: Path) -> dict[str, Any]:
    contract_report = check_contract(result_path, family)
    audit_report = check_audit(result_path, base)
    overall_ok = contract_report.get("ok", False) and audit_report.get("ok", False)
    return {
        "ok": overall_ok,
        "contract": contract_report,
        "audit": audit_report,
        "result_path": str(result_path),
        "family": family,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-check result.json")
    parser.add_argument("result", type=Path, help="path to result.json")
    parser.add_argument("--family", default="reading",
                        help="contract family (default: reading)")
    parser.add_argument("--base", type=Path, default=None,
                        help="base directory for resolving relative paths (default: result parent)")
    args = parser.parse_args()
    base = args.base or args.result.parent
    report = run_selfcheck(args.result, args.family, base)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
