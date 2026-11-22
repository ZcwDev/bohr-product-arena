#!/usr/bin/env python3
"""Self-check for the weekly-skill: run the pipeline on demo and empty-shell
inputs, validate contract compliance, verify artifacts exist and are non-trivial.

Exit 0 when every check passes, 1 otherwise.

Usage:
  python3 selfcheck.py [--demo-dir DIR] [--empty-dir DIR] [--out-root DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)


def _run_pipeline(input_dir: str, output_dir: str, label: str) -> dict:
    """Run run.py and return {exit_code, stdout, stderr, elapsed}."""
    cmd = [sys.executable, os.path.join(HERE, "run.py.py"),
           "--input", input_dir, "--output", output_dir]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    elapsed = time.time() - t0
    return {
        "label": label,
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "elapsed_s": round(elapsed, 2),
    }


def _check_result_json(result_path: str, expected_case_id: str, expect_substantive: bool) -> list[str]:
    """Validate result.json structure and content. Returns list of problems."""
    problems = []
    if not os.path.isfile(result_path):
        return [f"result.json missing at {result_path}"]

    try:
        with open(result_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return [f"result.json not valid JSON: {exc}"]

    # Contract fields for writing family
    required = ["case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"]
    for key in required:
        if key not in data:
            problems.append(f"missing field {key!r}")
        elif data[key] is None or (isinstance(data[key], (list, dict)) and len(data[key]) == 0):
            problems.append(f"field {key!r} is empty")

    # case_id must match
    if data.get("case_id") != expected_case_id:
        problems.append(f"case_id mismatch: got {data.get('case_id')!r}, expected {expected_case_id!r}")

    # Status must be one of the allowed values
    status = data.get("status")
    allowed = {"answered", "partial", "insufficient_evidence", "input_error"}
    if status not in allowed:
        problems.append(f"status {status!r} not in {allowed}")

    # Substantive checks when expected
    if expect_substantive:
        pf = data.get("preserved_facts", [])
        if not isinstance(pf, list) or len(pf) < 3:
            problems.append(f"preserved_facts too few ({len(pf) if isinstance(pf, list) else 'not list'}); expected substantive extraction")
        em = data.get("evidence_map", [])
        if not isinstance(em, list) or len(em) < 1:
            problems.append(f"evidence_map too few ({len(em) if isinstance(em, list) else 'not list'})")
        cl = data.get("change_log", [])
        if not isinstance(cl, list) or len(cl) < 3:
            problems.append(f"change_log too few ({len(cl) if isinstance(cl, list) else 'not list'})")
        # Check that at least some evidence has verified_in_draft=True
        verified = sum(1 for e in em if isinstance(e, dict) and e.get("verified_in_draft"))
        if verified == 0:
            problems.append("no evidence_map entry has verified_in_draft=True")

    return problems


def _check_artifacts(output_dir: str, case_id: str, expect_substantive: bool) -> list[str]:
    """Check that expected artifact files exist and are non-trivial."""
    problems = []

    docx_path = os.path.join(output_dir, f"{case_id}.docx")
    if not os.path.isfile(docx_path):
        problems.append(f"DOCX missing: {docx_path}")
    else:
        size = os.path.getsize(docx_path)
        if size < 1000:
            problems.append(f"DOCX too small ({size} bytes); likely empty/trivial")

    draft_path = os.path.join(output_dir, "draft.md")
    if not os.path.isfile(draft_path):
        problems.append(f"draft.md missing: {draft_path}")
    else:
        size = os.path.getsize(draft_path)
        if expect_substantive and size < 500:
            problems.append(f"draft.md too small ({size} bytes) for substantive run")

    preview_dir = os.path.join(output_dir, "preview")
    if os.path.isdir(preview_dir):
        pngs = [f for f in os.listdir(preview_dir) if f.endswith(".png")]
        if expect_substantive and len(pngs) == 0:
            problems.append("preview directory has no PNG files")
        for png in pngs:
            p = os.path.join(preview_dir, png)
            if os.path.getsize(p) < 1000:
                problems.append(f"preview PNG {png} too small ({os.path.getsize(p)} bytes)")
    elif expect_substantive:
        problems.append("preview directory missing")

    engine_info = os.path.join(output_dir, "engine_info.json")
    if os.path.isfile(engine_info):
        try:
            ei = json.load(open(engine_info, encoding="utf-8"))
            docx_info = ei.get("docx", {})
            if not docx_info.get("engine"):
                problems.append("engine_info.json has no docx.engine")
        except Exception:
            problems.append("engine_info.json not valid JSON")
    else:
        problems.append("engine_info.json missing")

    return problems


def _check_naming_red_lines(output_dir: str) -> list[str]:
    """Ensure no red-line naming patterns appear in output."""
    problems = []
    red_prefixes = ["SYN-", "EDGE-", "TEST-", "REV-"]
    red_names = ["caseA", "caseB", "caseC", "output_edge", "output_missing",
                 "portability", "selftest"]

    for root, dirs, files in os.walk(output_dir):
        for name in files + dirs:
            for rp in red_prefixes:
                if name.startswith(rp):
                    problems.append(f"red-line prefix {rp!r} in {os.path.join(root, name)}")
            for rn in red_names:
                if rn.lower() in name.lower():
                    problems.append(f"red-line name {rn!r} in {os.path.join(root, name)}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo-dir", default=os.path.join(os.path.dirname(SKILL_ROOT), "demo"))
    ap.add_argument("--empty-dir", default=os.path.join(os.path.dirname(SKILL_ROOT), "inputs"))
    ap.add_argument("--out-root", default=os.path.join(os.path.dirname(SKILL_ROOT), "out"))
    args = ap.parse_args()

    all_pass = True
    results = []

    # ── Test 1: Demo input (substantive) ──────────────────────────────────
    demo_out = os.path.join(args.out_root, "demo_selfcheck")
    if os.path.isdir(demo_out):
        shutil.rmtree(demo_out)

    if not os.path.isdir(args.demo_dir):
        results.append({"label": "demo_setup", "pass": False,
                        "problems": [f"demo directory missing: {args.demo_dir}"]})
        all_pass = False
    else:
        run = _run_pipeline(args.demo_dir, demo_out, "demo")
        if run["exit_code"] != 0:
            results.append({"label": "demo_run", "pass": False,
                            "problems": [f"exit code {run['exit_code']}", run["stderr"][:500]]})
            all_pass = False
        else:
            probs = []
            probs += _check_result_json(os.path.join(demo_out, "result.json"),
                                        "DEMO-CASE-001", expect_substantive=True)
            probs += _check_artifacts(demo_out, "DEMO-CASE-001", expect_substantive=True)
            probs += _check_naming_red_lines(demo_out)
            ok = len(probs) == 0
            results.append({"label": "demo", "pass": ok, "problems": probs,
                            "elapsed_s": run["elapsed_s"]})
            if not ok:
                all_pass = False

    # ── Test 2: Empty-shell input ─────────────────────────────────────────
    empty_out = os.path.join(args.out_root, "empty_selfcheck")
    if os.path.isdir(empty_out):
        shutil.rmtree(empty_out)

    run = _run_pipeline(args.empty_dir, empty_out, "empty")
    if run["exit_code"] != 0:
        results.append({"label": "empty_run", "pass": False,
                        "problems": [f"exit code {run['exit_code']}", run["stderr"][:500]]})
        all_pass = False
    else:
        probs = []
        probs += _check_result_json(os.path.join(empty_out, "result.json"),
                                    "EXAMPLE-NOT-A-TEST-CASE", expect_substantive=False)
        probs += _check_artifacts(empty_out, "EXAMPLE-NOT-A-TEST-CASE", expect_substantive=False)
        probs += _check_naming_red_lines(empty_out)
        ok = len(probs) == 0
        results.append({"label": "empty", "pass": ok, "problems": probs,
                        "elapsed_s": run["elapsed_s"]})
        if not ok:
            all_pass = False

    # ── Test 3: Module importability ──────────────────────────────────────
    import_probs = []
    for mod_name in ["contract", "evidence", "text_checks", "make_blocks",
                     "ooxml", "preview", "paper", "figure", "render",
                     "summary_card", "gain_report"]:
        mod_path = os.path.join(HERE, f"{mod_name}.py")
        if not os.path.isfile(mod_path):
            import_probs.append(f"module file missing: {mod_name}.py")
            continue
        try:
            proc = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {HERE!r}); import {mod_name}"],
                                  capture_output=True, text=True, timeout=10)
            if proc.returncode != 0:
                import_probs.append(f"import {mod_name} failed: {proc.stderr[:200]}")
        except Exception as exc:
            import_probs.append(f"import {mod_name} exception: {exc}")
    ok = len(import_probs) == 0
    results.append({"label": "imports", "pass": ok, "problems": import_probs})
    if not ok:
        all_pass = False

    # ── Report ─────────────────────────────────────────────────────────────
    report = {"pass": all_pass, "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
