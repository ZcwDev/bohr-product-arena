#!/bin/sh
# Academic Paper Writing Skill - Main Entry Point
# Priority: produce substantive artifacts, never exception-exit
#
# Usage: python3 run.py --input DIR --output DIR
# Default: --input /input --output /app/output
#
# This script:
# 1. Detects the right Python interpreter (venv with fitz/matplotlib/docx preferred)
# 2. Discovers case.json in input dir
# 3. Indexes materials (PDF/text/docx) with locator tracking
# 4. Plans sections, extracts facts, assembles draft
# 5. Generates editable docx + preview PNGs
# 6. Verifies number preservation via text_checks
# 7. Writes substantive result.json with all contract fields
# 8. Records engine info for RUN.md

PY=${WEEKLY_TOOLS_PYTHON:-/opt/weekly-tools/bin/python}
[ -x "$PY" ] || PY=python3
exec "$PY" "$(dirname "$0")/run.py" "$@"
