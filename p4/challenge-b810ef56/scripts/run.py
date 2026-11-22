#!/usr/bin/env python3
"""Entry wrapper for the evidence-bound writing skill.

Translates the platform convention ``--input DIR --output DIR`` into the
underlying ``run_case.py --case <path> --output <path>`` invocation.

Interpreter selection:
  The pinned evaluation image ships a curated venv at /opt/weekly-tools.
  If available it is preferred for richer PDF / OOXML support.  We
  transparently re-exec under it when the current interpreter lacks it.

Usage::

    python3 skill/scripts/run.py --input /input --output /app/output
    python3 skill/scripts/run.py --input ./demo --output ./out
"""
from __future__ import annotations
# --- evaluator-environment bootstrap (injected) ---
# The replay image keeps the science stack (fitz / matplotlib / python-docx /
# python-pptx / playwright / CairoSVG) inside its own venv and points at it with
# WEEKLY_TOOLS_PYTHON; a bare `python3` is miniconda's and may not have them, which
# is how a run ends up with no rendered previews and no PDF parsing. Re-exec under
# that interpreter when it is present, and stay put when it is not.
import os as _os, sys as _sys
_TOOLS_PY = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
if (_os.path.exists(_TOOLS_PY) and not _os.environ.get("_SKILL_REEXEC")
        and _os.path.realpath(_sys.executable) != _os.path.realpath(_TOOLS_PY)):
    _os.environ["_SKILL_REEXEC"] = "1"
    try:
        _os.execv(_TOOLS_PY, [_TOOLS_PY, _os.path.abspath(__file__)] + _sys.argv[1:])
    except OSError:
        pass
# --- end evaluator-environment bootstrap ---

import argparse
import os
import sys
from pathlib import Path


def _select_interpreter() -> None:
    """Re-exec under the weekly-tools venv if we are not already there."""
    weekly = os.environ.get("WEEKLY_TOOLS_PYTHON", "/opt/weekly-tools/bin/python")
    if os.path.isabs(weekly) and os.path.isfile(weekly) and os.access(weekly, os.X_OK):
        if os.path.realpath(sys.executable) != os.path.realpath(weekly):
            os.execv(weekly, [weekly] + sys.argv)


def _find_case(input_dir: Path) -> Path:
    """Locate case.json or the first JSON file inside the input directory."""
    preferred = input_dir / "case.json"
    if preferred.is_file():
        return preferred
    candidates = sorted(input_dir.glob("*.json"))
    if candidates:
        return candidates[0]
    # Fallback: look one level deeper
    for sub in sorted(input_dir.iterdir()):
        if sub.is_dir():
            nested = sub / "case.json"
            if nested.is_file():
                return nested
    # Nothing found: return the preferred path so run_case.py handles it
    return preferred


def main() -> int:
    _select_interpreter()
    parser = argparse.ArgumentParser(description="Evidence-bound writing skill entry point")
    parser.add_argument("--input", default="/input", help="Input directory containing case.json")
    parser.add_argument("--output", default="/app/output", help="Output directory for result.json and artifacts")
    parser.add_argument("--work", default=None, help="Working directory (default: <output>/../work)")
    parser.add_argument("--authoring", default=None, help="Optional evidence-backed authoring JSON file")
    parser.add_argument("--reuse-index", action="store_true", help="Reuse previously built blocks index")
    parser.add_argument("--max-seconds", type=int, default=105, help="Internal soft budget")
    args = parser.parse_args()

    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.work:
        work_dir = Path(args.work).resolve()
    else:
        work_dir = (output_dir.parent / "work").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    case_path = _find_case(input_dir)
    here = Path(__file__).resolve().parent
    runner = here / "run_case.py"

    cmd = [sys.executable, str(runner),
           "--case", str(case_path),
           "--input-root", str(input_dir),
           "--work", str(work_dir),
           "--output", str(output_dir),
           "--max-seconds", str(args.max_seconds)]
    if args.authoring:
        cmd.extend(["--authoring", args.authoring])
    if args.reuse_index:
        cmd.append("--reuse-index")

    os.execv(sys.executable, cmd)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
