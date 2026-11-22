#!/usr/bin/env bash
# End-to-end orchestrator for the reading-family skill.
#
# Reads /input/case.json + /input/target-fields.json, extracts from material,
# assembles /app/output/result.json, runs self-check.
#
# Usage: bash run_case.sh [--work WORK_DIR] [--output OUTPUT_DIR] [--skill SKILL_DIR]
#
# Environment variables (override flags):
#   SKILL_DIR  default: /skills/weekly-skill (or auto-detected)
#   WORK       default: /app/work
#   OUTPUT     default: /app/output
#
# Exit 0: all stages succeeded, contract check clean.
# Exit 1: at least one stage failed (see EXIT= lines).
#
# The agent's role in step 5 is to fill fields_filled.json using the scaffold +
# evidence from step 4. This script handles steps 1-4 and 6-8 automatically;
# step 5 requires agent intervention (or the synthetic example path).
set -u

# ---------- Defaults & argument parsing ----------
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
WORK="${WORK:-/app/work}"
OUTPUT="${OUTPUT:-/app/output}"
INPUT_DIR="${INPUT_DIR:-/input}"

while [ $# -gt 0 ]; do
  case "$1" in
    --skill)  SKILL_DIR="$2"; shift 2 ;;
    --work)   WORK="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --input)  INPUT_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

# ---------- Setup ----------
mkdir -p "$WORK" "$OUTPUT"
# Prefer the weekly-tools venv (has fitz/pymupdf/matplotlib/etc.)
PY="${WEEKLY_TOOLS_PYTHON:-}"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  if [ -x "/opt/weekly-tools/bin/python" ]; then
    PY="/opt/weekly-tools/bin/python"
  else
    PY="python3"
  fi
fi
echo "Using Python: $PY ($("$PY" --version 2>&1))"

step() { printf '\n=== %s\n' "$*"; }
fail=0

run() {
  step "$*"
  "$@"
  code=$?
  echo "EXIT=$code"
  [ "$code" -ne 0 ] && fail=1
  return "$code"
}

# ---------- Auto-detect SKILL_DIR if not found ----------
if [ ! -f "$SKILL_DIR/scripts/case_io.py" ]; then
  ALT=$(find / -name case_io.py -not -path '*/node_modules/*' 2>/dev/null | head -1)
  if [ -n "$ALT" ]; then
    SKILL_DIR=$(dirname "$(dirname "$ALT")")
  else
    echo "ERROR: cannot find skill scripts directory"
    exit 1
  fi
fi
echo "SKILL_DIR=$SKILL_DIR"
echo "WORK=$WORK"
echo "OUTPUT=$OUTPUT"
echo "INPUT_DIR=$INPUT_DIR"

# ---------- Step 1: Read case contract ----------
run "$PY" "$SKILL_DIR"/scripts/case_io.py read \
    --case "$INPUT_DIR/case.json" \
    --targets "$INPUT_DIR/target-fields.json" \
    --outdir "$WORK" \
    --material-dir "$INPUT_DIR"
if [ $? -ne 0 ]; then
  echo "Step 1 failed: cannot read case contract. Exiting."
  exit 1
fi

# ---------- Detect material ----------
MATERIAL=""
if [ -f "$WORK/material_index.json" ]; then
  MATERIAL=$("$PY" -c "
import json, os
INPUT_DIR = '$INPUT_DIR'
idx = json.load(open('$WORK/material_index.json', encoding='utf-8'))
files = idx.get('files', idx.get('materials', []))
for f in files:
    name = f.get('name', f.get('path', ''))
    p = os.path.join(INPUT_DIR, name) if not os.path.isabs(name) else name
    if os.path.isfile(p) and name.lower().endswith('.pdf'):
        print(p); break
else:
    # try common names
    for n in ['material.pdf', 'paper.pdf', 'source.pdf', 'synthetic-paper.pdf']:
        p = os.path.join(INPUT_DIR, n)
        if os.path.isfile(p): print(p); break
    else:
        for n in ['material.txt', 'paper.txt']:
            p = os.path.join(INPUT_DIR, n)
            if os.path.isfile(p): print(p); break
")
fi
echo "MATERIAL=$MATERIAL"

# ---------- Step 2: Extract PDF text ----------
if [ -n "$MATERIAL" ] && [[ "$MATERIAL" == *.pdf ]]; then
  run "$PY" "$SKILL_DIR"/scripts/pdf_text.py extract "$MATERIAL" \
      -o "$WORK/blocks.json" --jsonl "$WORK/blocks.jsonl"
else
  echo "Step 2 skipped: no PDF material found (MATERIAL=$MATERIAL)"
  # Create minimal blocks.json from material.txt if available
  if [ -f "$WORK/material.txt" ] || [ -f "/input/material.txt" ]; then
    MTXT="${WORK}/material.txt"
    [ -f "$MTXT" ] || MTXT="/input/material.txt"
    "$PY" - "$WORK" "$MTXT" <<'PYBLOCKS'
import json, os, sys
work, mtxt = sys.argv[1], sys.argv[2]
text = open(mtxt, encoding="utf-8", errors="replace").read()
paras = [p.strip() for p in text.split("\n\n") if p.strip()]
blocks = []
for i, p in enumerate(paras):
    blocks.append({"id": f"p1b{i+1}", "page": 1, "block": i+1, "text": p, "type": "body"})
json.dump({"blocks": blocks, "meta": {"engine": "text_fallback", "source": mtxt}},
          open(os.path.join(work, "blocks.json"), "w"), ensure_ascii=False, indent=1)
PYBLOCKS
  fi
fi

# ---------- Step 3: Extract tables ----------
if [ -n "$MATERIAL" ] && [[ "$MATERIAL" == *.pdf ]]; then
  run "$PY" "$SKILL_DIR"/scripts/pdf_text.py tables "$MATERIAL" -o "$WORK/tables.json"
  echo "(null engine here is an honest degradation, not a pipeline failure)"
fi

# ---------- Step 4: Locate candidate paragraphs for each field ----------
if [ -f "$WORK/fields_scaffold.json" ] && [ -f "$WORK/blocks.json" ]; then
  step "4. Locate candidates per field_id"
  "$PY" - "$WORK" "$SKILL_DIR" <<'PYLOCATE'
import json, os, subprocess, sys

work = sys.argv[1]
skill_dir = sys.argv[2]

scaffold = json.load(open(os.path.join(work, "fields_scaffold.json"), encoding="utf-8"))
fields = scaffold.get("fields", scaffold) if isinstance(scaffold, dict) else scaffold

results = []
for f in (fields if isinstance(fields, list) else []):
    fid = f.get("field_id", "")
    label = f.get("label", fid) or fid
    aliases = f.get("aliases", []) or []
    search_terms = [label] + [a for a in aliases if a]
    for term in search_terms[:3]:
        r = subprocess.run(
            ["python3", os.path.join(skill_dir, "scripts", "evidence.py"),
             "locate", os.path.join(work, "blocks.json"), term, "--limit", "5"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            results.append({"field_id": fid, "search": term, "hits": r.stdout.strip()})
            break

json.dump(results, open(os.path.join(work, "locate_results.json"), "w"),
          ensure_ascii=False, indent=1)
print(f"Located {len(results)} fields")
PYLOCATE
fi

# ---------- Step 5: Agent fills fields (or synthetic path) ----------
step "5. Fill fields_filled.json"

if [ ! -f "$WORK/fields_filled.json" ]; then
  echo "Step 5: fields_filled.json not found — agent must fill it."
  echo "Use the scaffold at $WORK/fields_scaffold.json and evidence at $WORK/locate_results.json"
  echo "Each field needs: scalar, units, statistic, condition, comparator, status, paragraph_id, quote, semantic_check"
  fail=1
else
  echo "Step 5: fields_filled.json already present"
fi

if [ "$fail" -ne 0 ]; then
  echo
  echo "RESULT: BLOCKED at step 5 — agent must fill fields_filled.json"
  exit 1
fi

# ---------- Step 6: Field gate check ----------
run "$PY" "$SKILL_DIR"/scripts/fields_check.py check "$WORK/fields_filled.json" \
    --scaffold "$WORK/fields_scaffold.json" \
    --blocks "$WORK/blocks.json" \
    -o "$WORK/fields_check.json"

if [ $? -ne 0 ]; then
  echo "Step 6 failed: fields_check found problems. Fix fields_filled.json and retry."
  cat "$WORK/fields_check.json" 2>/dev/null
  exit 1
fi

# ---------- Step 7: Assemble result ----------
# Backup if previous result exists
if [ -f "$OUTPUT/result.json" ]; then
  cp "$OUTPUT/result.json" "$WORK/result.prev.json"
fi

run "$PY" "$SKILL_DIR"/scripts/assemble.py build \
    --context "$WORK/case_context.json" \
    --fields "$WORK/fields_filled.json" \
    --out "$OUTPUT/result.json"

# ---------- Step 8: Contract check ----------
run "$PY" "$SKILL_DIR"/scripts/contract.py check "$OUTPUT/result.json" --family reading

if [ "$fail" -ne 0 ]; then
  echo
  echo "RESULT: FAILED — at least one stage returned non-zero"
  exit 1
fi

echo
echo "RESULT: OK — all stages exited 0, contract check clean"
exit 0
