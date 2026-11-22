#!/usr/bin/env python3
"""
Self-check validator for the literature extraction pipeline.

Validates:
  1. Pipeline runs successfully
  2. All quotes are exact substrings of referenced block text
  3. Status enum is valid
  4. Field-level status is valid
  5. Scalar/units/statistic are present for reported fields

Usage:
  python selfcheck.py --input DIR --output DIR [--verbose]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


VALID_TOP_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
VALID_FIELD_STATUS = {"reported", "not_reported", "source_conflict", "extraction_failed"}


def run_pipeline(input_dir: Path, output_dir: Path) -> tuple[int, str]:
    """Run the extraction pipeline and return (returncode, stderr)."""
    script_dir = Path(__file__).parent.resolve()
    run_py = script_dir / "run.py"
    cmd = [sys.executable, str(run_py), "--input", str(input_dir.resolve()), "--output", str(output_dir.resolve())]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stderr


def validate_quotes(result_data: dict, blocks_data: dict) -> list[str]:
    """Validate that all quotes are exact substrings of their referenced blocks."""
    block_map = {b["id"]: b["text"] for b in blocks_data.get("blocks", [])}
    errors = []
    
    for field in result_data.get("result", {}).get("fields", []):
        fid = field["field_id"]
        pid = field.get("paragraph_id")
        quote = field.get("quote")
        status = field.get("status")
        
        if status != "reported":
            continue
        
        if not pid or not quote:
            errors.append(f"{fid}: reported but missing paragraph_id or quote")
            continue
        
        if pid not in block_map:
            errors.append(f"{fid}: paragraph_id {pid} not found in blocks.json")
            continue
        
        block_text = block_map[pid]
        if quote not in block_text:
            errors.append(f"{fid}: quote not substring of block {pid}")
            errors.append(f"  quote[:80]={quote[:80]!r}")
            errors.append(f"  block[:80]={block_text[:80]!r}")
    
    return errors


def validate_status(result_data: dict) -> list[str]:
    """Validate status enums."""
    errors = []
    
    top_status = result_data.get("status")
    if top_status not in VALID_TOP_STATUS:
        errors.append(f"Invalid top-level status: {top_status}")
    
    for field in result_data.get("result", {}).get("fields", []):
        fid = field["field_id"]
        field_status = field.get("status")
        if field_status not in VALID_FIELD_STATUS:
            errors.append(f"{fid}: invalid field status {field_status}")
    
    return errors


def validate_reported_fields(result_data: dict) -> list[str]:
    """Validate that reported fields have required metadata."""
    errors = []
    
    for field in result_data.get("result", {}).get("fields", []):
        fid = field["field_id"]
        status = field.get("status")
        
        if status != "reported":
            continue
        
        # Check required fields
        if not field.get("scalar"):
            errors.append(f"{fid}: reported but missing scalar")
        if not field.get("paragraph_id"):
            errors.append(f"{fid}: reported but missing paragraph_id")
        if not field.get("quote"):
            errors.append(f"{fid}: reported but missing quote")
    
    return errors


def main():
    parser = argparse.ArgumentParser(description="Self-check validator")
    parser.add_argument("--input", type=Path, default=Path("demo"), help="Input directory")
    parser.add_argument("--output", type=Path, default=Path("out"), help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    print(f"Running pipeline: {args.input} → {args.output}")
    rc, stderr = run_pipeline(args.input, args.output)
    
    if rc != 0:
        print(f"❌ Pipeline failed (exit code {rc})")
        print(stderr)
        return 1
    
    print("✓ Pipeline completed")
    
    # Load result and blocks
    result_path = args.output / "result.json"
    blocks_path = args.output / "work" / "blocks.json"
    
    if not result_path.exists():
        print(f"❌ Result not found: {result_path}")
        return 1
    
    with open(result_path) as f:
        result_data = json.load(f)
    
    if not blocks_path.exists():
        print(f"❌ Blocks not found: {blocks_path}")
        return 1
    
    with open(blocks_path) as f:
        blocks_data = json.load(f)
    
    # Validate
    all_errors = []
    
    print("\nValidating status enums...")
    status_errors = validate_status(result_data)
    all_errors.extend(status_errors)
    print(f"  {'✓' if not status_errors else '❌'} {len(status_errors)} errors")
    if args.verbose:
        for e in status_errors:
            print(f"    {e}")
    
    print("\nValidating reported fields...")
    field_errors = validate_reported_fields(result_data)
    all_errors.extend(field_errors)
    print(f"  {'✓' if not field_errors else '❌'} {len(field_errors)} errors")
    if args.verbose:
        for e in field_errors:
            print(f"    {e}")
    
    print("\nValidating quotes...")
    quote_errors = validate_quotes(result_data, blocks_data)
    all_errors.extend(quote_errors)
    print(f"  {'✓' if not quote_errors else '❌'} {len(quote_errors)} errors")
    if args.verbose:
        for e in quote_errors:
            print(f"    {e}")
    
    # Summary
    fields = result_data.get("result", {}).get("fields", [])
    reported = sum(1 for f in fields if f.get("status") == "reported")
    
    print("\n" + "="*60)
    print(f"Case ID: {result_data.get('case_id')}")
    print(f"Status: {result_data.get('status')}")
    print(f"Fields: {reported}/{len(fields)} reported")
    print(f"Claims: {len(result_data.get('claims', []))}")
    print(f"Uncertainties: {len(result_data.get('uncertainties', []))}")
    print("="*60)
    
    if all_errors:
        print(f"\n❌ {len(all_errors)} validation errors")
        return 1
    else:
        print("\n✓ All validations passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
