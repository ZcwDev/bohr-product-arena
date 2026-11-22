#!/usr/bin/env bash
# Entry script for the academic-writing benchmark skill (one case per call).
#
# Usage (from ANY working directory):
#   bash <skill-dir>/run.sh prepare  [--input CASE_JSON] [--output DIR] [--work DIR]
#   bash <skill-dir>/run.sh finalize [--input CASE_JSON] [--output DIR] [--work DIR] [--font NAME]
#   bash <skill-dir>/run.sh all      ... (prepare, then finalize whatever draft.md exists)
#
# Defaults: --input /input/case.json  --output /app/output  --work /app/work
# (env INPUT_CASE / OUTPUT_DIR / WORK_DIR override them as well).
#
# There is NO fallback input: when the case file is missing or unreadable the
# script writes result.json with status=input_error and exits 2.
#
# Exit codes: 0 finished (read result.json "status"); 2 input_error / usage.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$SCRIPT_DIR/scripts"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/mpl-weekly-skill}"
mkdir -p "$MPLCONFIGDIR" 2>/dev/null || true

MODE="${1:-all}"; [ $# -gt 0 ] && shift
INPUT_CASE="${INPUT_CASE:-/input/case.json}"
OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
WORK_DIR="${WORK_DIR:-/app/work}"
FONT_ARGS=()
BUDGET="${BUDGET:-110}"

while [ $# -gt 0 ]; do
  case "$1" in
    --input)  INPUT_CASE="$2"; shift 2;;
    --output) OUTPUT_DIR="$2"; shift 2;;
    --work)   WORK_DIR="$2";   shift 2;;
    --font)   FONT_ARGS=(--font "$2"); shift 2;;
    --budget) BUDGET="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
case "$MODE" in prepare|finalize|all) ;; *) echo "mode must be prepare|finalize|all" >&2; exit 2;; esac

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"
echo "[run.sh] mode=$MODE input=$INPUT_CASE output=$OUTPUT_DIR work=$WORK_DIR" >&2

if [ "$MODE" = "prepare" ] || [ "$MODE" = "all" ]; then
  python3 "$SCRIPTS/run_case.py" prepare --case "$INPUT_CASE" --work "$WORK_DIR" --output "$OUTPUT_DIR"
  rc=$?
  [ $rc -ne 0 ] && { echo "[run.sh] prepare exit=$rc (input_error written to result.json)" >&2; exit $rc; }
fi
if [ "$MODE" = "finalize" ] || [ "$MODE" = "all" ]; then
  python3 "$SCRIPTS/run_case.py" finalize --case "$INPUT_CASE" --work "$WORK_DIR" --output "$OUTPUT_DIR" \
    --budget "$BUDGET" ${FONT_ARGS[@]+"${FONT_ARGS[@]}"}
  rc=$?
  [ $rc -ne 0 ] && { echo "[run.sh] finalize exit=$rc" >&2; exit $rc; }
fi
echo "[run.sh] done: $OUTPUT_DIR/result.json" >&2
exit 0
