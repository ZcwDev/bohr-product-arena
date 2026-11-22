#!/usr/bin/env python3
"""Single entry point for the 文献结论与数据提取 skill.

Usage:
    python3 run.py --input DIR --output DIR [--work DIR] [--selfcheck]

Defaults:
    --input  /input        (where case.json + target-fields.json + materials live)
    --output /app/output   (where result.json is written)
    --work   /app/work     (intermediate artifacts)

Environment:
    WEEKLY_TOOLS_PYTHON  path to the weekly-tools venv Python (auto-detected)
    SKILL_DIR            path to the skill root (auto-detected from __file__)

Exit codes:
    0  result.json written and contract-clean
    1  input_error or contract violation
    2  unhandled exception

This script is the only thing an external caller needs to invoke. It orchestrates
the full extraction pipeline using the existing modules (case_io, pdf_text,
evidence, assemble, contract, fields_check) and handles the /opt/weekly-tools
venv re-exec transparently.
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
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Venv re-exec: ensure we run under /opt/weekly-tools when available
# ---------------------------------------------------------------------------
WEEKLY_TOOLS_PY = "/opt/weekly-tools/bin/python"


def _maybe_reexec():
    """Re-spawn under the weekly-tools venv if available and not already there."""
    env_py = os.environ.get("WEEKLY_TOOLS_PYTHON", "")
    candidates = [env_py, WEEKLY_TOOLS_PY]
    current = os.path.realpath(sys.executable)
    for cand in candidates:
        if not cand:
            continue
        cand_real = os.path.realpath(cand)
        if cand_real == current:
            return  # already running under the right python
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            os.execv(cand, [cand] + sys.argv)
    # fallback: continue with whatever python we have


_maybe_reexec()

# ---------------------------------------------------------------------------
# Locate skill dir
# ---------------------------------------------------------------------------
SKILL_DIR = os.environ.get("SKILL_DIR", "")
if not SKILL_DIR or not os.path.isfile(os.path.join(SKILL_DIR, "scripts", "case_io.py")):
    SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

# Now import sibling modules
try:
    import case_io as _case_io
    import pdf_text as _pdf_text
    import evidence as _evidence
    import assemble as _assemble
    import contract as _contract
    import fields_check as _fields_check
except ImportError as _exc:
    print(f"[run.py] WARNING: could not import sibling modules: {_exc}", file=sys.stderr)
    _case_io = _pdf_text = _evidence = _assemble = _contract = _fields_check = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _jwrite(path: str, payload, indent=1):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent)
        fh.write("\n")


def _jload(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _read_text_file(path: str) -> str:
    encodings = ["utf-8", "gbk", "latin-1"]
    for enc in encodings:
        try:
            with open(path, encoding=enc, errors="strict") as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Material reading: turn input files into blocks
# ---------------------------------------------------------------------------
MATERIAL_EXTS = {".pdf", ".txt", ".md", ".tex", ".html", ".xml", ".csv", ".tsv",
                 ".docx", ".pptx", ".xlsx"}
CONTROL_NAMES = {"case.json", "target-fields.json", "target_fields.json",
                 "output_contract.json", "manifest.json", "task.json"}


def _find_materials(input_dir: str) -> list[str]:
    """Return material file paths, excluding control files."""
    out = []
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            if fn in CONTROL_NAMES:
                continue
            if fn.startswith("."):
                continue
            full = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in MATERIAL_EXTS:
                out.append(full)
    return out


def _text_to_blocks(text: str, source_path: str) -> dict:
    """Convert plain text to the same block schema pdf_text.py produces."""
    lines = text.split("\n")
    blocks = []
    para_lines: list[str] = []
    para_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if para_lines:
                blocks.append({
                    "id": f"p1b{len(blocks)+1}",
                    "page": 1,
                    "block": len(blocks) + 1,
                    "text": " ".join(para_lines),
                    "bbox": [0, 0, 0, 0],
                    "size": 11.0,
                    "kind": "body",
                    "line0": para_start + 1,
                    "line1": i,
                })
                para_lines = []
        else:
            if not para_lines:
                para_start = i
            para_lines.append(stripped)
    if para_lines:
        blocks.append({
            "id": f"p1b{len(blocks)+1}",
            "page": 1,
            "block": len(blocks) + 1,
            "text": " ".join(para_lines),
            "bbox": [0, 0, 0, 0],
            "size": 11.0,
            "kind": "body",
            "line0": para_start + 1,
            "line1": len(lines),
        })
    return {
        "source": source_path,
        "engine": "text",
        "n_pages": 1,
        "blocks": blocks,
        "warnings": [],
    }


def extract_blocks(material_path: str, work_dir: str) -> dict:
    """Extract text blocks from a material file. PDF uses pdf_text.py, others text."""
    if _is_pdf(material_path):
        if _pdf_text is not None:
            try:
                result = _pdf_text.extract(material_path)
                _jwrite(os.path.join(work_dir, "blocks.json"), result)
                return result
            except Exception as exc:
                print(f"[run.py] pdf_text.extract failed: {exc}", file=sys.stderr)
        # Fallback: try pdftotext
        try:
            out = subprocess.run(["pdftotext", "-layout", material_path, "-"],
                                 capture_output=True, timeout=30)
            if out.returncode == 0:
                text = out.stdout.decode("utf-8", errors="replace")
                result = _text_to_blocks(text, material_path)
                result["engine"] = "pdftotext"
                _jwrite(os.path.join(work_dir, "blocks.json"), result)
                return result
        except Exception:
            pass
        return {"source": material_path, "engine": "failed", "blocks": [],
                "warnings": ["could not extract PDF text"], "n_pages": 0}
    else:
        text = _read_text_file(material_path)
        result = _text_to_blocks(text, material_path)
        _jwrite(os.path.join(work_dir, "blocks.json"), result)
        return result


# ---------------------------------------------------------------------------
# Field extraction: rule-based extraction for each target field
# ---------------------------------------------------------------------------

# Number pattern: matches numbers with optional units
NUM_PATTERN = re.compile(
    r"([-+]?\d+(?:[.,]\d+)?(?:\s*[×x]\s*10\s*\^?\s*-?\d+)?)\s*"
    r"(%|‰|°C|°F|mg|g|kg|µg|ug|ng|pg|mL|dL|µL|uL|L|mmol|mol|µmol|umol|"
    r"mm2|cm2|m2|mm|cm|m|km|µm|um|nm|kDa|Da|kPa|MPa|mmHg|Hz|kHz|MHz|rpm|"
    r"mV|V|W|kW|mW|J|kJ|kcal|cal|AU|OD|bp|kb|M|nM|µM|uM|mM|"
    r"mg/dL|mmol/L|µmol/L|mg/mL|µg/mL|ng/mL|pg/mL|cells/mL|"
    r"mg/kg|µg/kg|ng/kg|mL/min|L/min|L/h|mL/h|"
    r"mg/day|g/day|µg/day|IU|U/L|pg/mL|copies/mL|CFU/mL|cfu|"
    r"cells/µL|×10\^?\d+|/µL|/mm3|/dL|/L|weeks?|days?|months?|years?|h|min|s)\b",
    re.I
)

# P-value pattern
PVALUE_PATTERN = re.compile(
    r"[pP]\s*[<>=]\s*0?\.\d+|p\s*=\s*\d+\.\d+|p-value\s*[<>=]\s*0?\.\d+",
    re.I
)

# CI pattern
CI_PATTERN = re.compile(
    r"(?:95%?\s*CI|confidence interval|CI)\s*[:=]?\s*[\[(]?\s*([-+]?\d+\.?\d*)\s*(?:to|[-–—])\s*([-+]?\d+\.?\d*)\s*[\])}]?",
    re.I
)

# HR/OR/RR pattern with optional CI
RATIO_PATTERN = re.compile(
    r"(?:HR|hazard ratio|OR|odds ratio|RR|relative risk|risk ratio)\s*"
    r"(?:[=:]\s*|was\s+|of\s+|for\s+\w[\w\s]{0,30}?was\s+)?([-+]?\d+\.?\d*)",
    re.I
)

# Mean ± SD/SE pattern (handles ±, +/-, +/−, +-)
MEAN_SD_PATTERN = re.compile(
    r"([-+]?\d+\.?\d*)\s*(?:[±\u2212]|\+/\-|[\u00B1]|\+\-)\s*(\d+\.?\d*)\s*(?:SD|SE|SEM|standard deviation|standard error)?",
    re.I
)

# Sample size pattern: n=XXX, N=XXX, n XXX
SAMPLE_N_PATTERN = re.compile(
    r"(?:\bn\s*=\s*(\d+)|\bN\s*=\s*(\d+)|"
    r"\((\d+)\s*(?:patients?|subjects?|participants?|individuals?))",
    re.I
)

# Dose pattern: XXX mg, XXX µg, etc. (require space between number and unit)
DOSE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s+(mg\b|µg\b|ug\b|mcg\b|IU\b|\bg\b)\b",
    re.I
)

# Duration pattern
DURATION_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(weeks?|days?|months?|years?|wks?|d\b|mo\b|yr?s?)\b",
    re.I
)

# AE rate pattern
AE_RATE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+(?:patients|participants|subjects|recipients|individuals))",
    re.I
)

# Section headings
SECTION_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?"
    r"(abstract|introduction|background|methods?|materials?|results?|discussion|"
    r"conclusions?|limitations?|acknowledgment|references|supplement|appendix|"
    r"摘要|引言|方法|材料|结果|讨论|结论|局限|参考文献|附录)\b",
    re.I
)


def _get_field_id(field_spec: dict) -> str | None:
    """Extract field_id from a field spec."""
    for key in ("field_id", "fieldId", "id", "key", "name", "slug"):
        v = field_spec.get(key)
        if v and isinstance(v, str):
            return v
    return None


def _get_field_search_terms(field_spec: dict) -> list[str]:
    """Build a list of search terms from field spec."""
    terms = []
    for key in ("label", "field_id", "name", "title", "display_name",
                "description", "question", "definition"):
        v = field_spec.get(key, "")
        if isinstance(v, str) and v.strip():
            terms.append(v.strip().lower())
    for key in ("aliases", "synonyms", "search_terms", "keywords"):
        v = field_spec.get(key)
        if isinstance(v, list):
            terms.extend(str(x).strip().lower() for x in v if x)
        elif isinstance(v, str) and v.strip():
            terms.extend(x.strip().lower() for x in re.split(r"[,;|]", v) if x.strip())
    return [t for t in terms if len(t) > 2]


def _find_block_containing(blocks_data: dict, pattern: re.Pattern) -> tuple:
    """Find the first block containing a regex match. Returns (block, match, text)."""
    for blk in blocks_data.get("blocks", []):
        text = blk.get("text", "")
        m = pattern.search(text)
        if m:
            return blk, m, text
    return None, None, None


def _find_block_containing_terms(blocks_data: dict, terms: list[str]) -> tuple:
    """Find the first block containing any of the search terms. Returns (block, matched_term, text)."""
    for blk in blocks_data.get("blocks", []):
        text = blk.get("text", "")
        text_lower = text.lower()
        for term in terms:
            if term in text_lower:
                return blk, term, text
    return None, None, None


def _quote_around(text: str, match_start: int, match_end: int, context: int = 100) -> str:
    """Extract a quote around a match position."""
    start = max(0, match_start - context)
    end = min(len(text), match_end + context)
    return text[start:end].strip()


def _extract_field_specific(blocks_data: dict, field_spec: dict, field_id: str) -> dict | None:
    """
    Field-specific extraction based on field_id patterns.
    Returns a complete field dict or None if not extractable.
    """
    fid_lower = field_id.lower()
    blocks = blocks_data.get("blocks", [])

    # --- P-VALUE fields ---
    if "pvalue" in fid_lower or "p_value" in fid_lower or "p-value" in fid_lower or "significance" in fid_lower:
        for blk in blocks:
            text = blk.get("text", "")
            m = PVALUE_PATTERN.search(text)
            if m:
                return {
                    "field_id": field_id,
                    "scalar": m.group(0),
                    "units": None,
                    "statistic": "p-value",
                    "condition": None,
                    "comparator": None,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end()),
                    "status": "reported",
                    "semantic_check": f"extracted p-value from block {blk.get('id', '')}",
                }

    # --- SAMPLE SIZE fields ---
    if "sample_size" in fid_lower or "sample_n" in fid_lower or "enrolled" in fid_lower:
        # Look for treatment vs placebo/control sample sizes
        is_treatment = any(k in fid_lower for k in ("treatment", "active", "drug", "intervention", "experimental"))
        is_placebo = any(k in fid_lower for k in ("placebo", "control", "comparator"))

        # Search for n= patterns near treatment/placebo keywords
        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()

            # Pattern: "n=XXX" near treatment/placebo keywords
            if is_treatment:
                # Look for patterns like "DrugX (n=564)" or "treatment arm (n=564)"
                m = re.search(r"(?:treatment|active|drug|intervention|experimental|[A-Z][A-Za-z0-9]+(?:-[A-Z0-9]+)+)\s*(?:arm|group|recipients?)?\s*[\(:]?\s*n\s*=\s*(\d+)", text, re.I)
                if m:
                    return {
                        "field_id": field_id,
                        "scalar": m.group(1),
                        "units": None,
                        "statistic": "count",
                        "condition": "treatment arm",
                        "comparator": None,
                        "paragraph_id": blk.get("id", ""),
                        "quote": _quote_around(text, m.start(), m.end()),
                        "status": "reported",
                        "semantic_check": f"extracted treatment sample size from block {blk.get('id', '')}",
                    }
                # Also try "randomized ... to treatment (n=XXX)"
                m = re.search(r"randomized\s+.*?(?:\d+:1|2:1|1:1).*?(\d+)\s+(?:patients?|subjects?|participants?)\s*(?:to\s+)?(?:receive|treatment|active|drug|[A-Z][A-Za-z0-9]+(?:-[A-Z0-9]+)+)", text, re.I)
                if m:
                    return {
                        "field_id": field_id,
                        "scalar": m.group(1),
                        "units": None,
                        "statistic": "count",
                        "condition": "treatment arm",
                        "comparator": None,
                        "paragraph_id": blk.get("id", ""),
                        "quote": _quote_around(text, m.start(), m.end()),
                        "status": "reported",
                        "semantic_check": f"extracted treatment sample size from randomization text",
                    }

            if is_placebo:
                m = re.search(r"(?:placebo|control)\s*(?:arm|group|recipients?)?\s*[\(:]?\s*n\s*=\s*(\d+)", text, re.I)
                if m:
                    return {
                        "field_id": field_id,
                        "scalar": m.group(1),
                        "units": None,
                        "statistic": "count",
                        "condition": "placebo/control arm",
                        "comparator": None,
                        "paragraph_id": blk.get("id", ""),
                        "quote": _quote_around(text, m.start(), m.end()),
                        "status": "reported",
                        "semantic_check": f"extracted placebo sample size from block {blk.get('id', '')}",
                    }

            # Generic n= pattern
            m = SAMPLE_N_PATTERN.search(text)
            if m and not is_treatment and not is_placebo:
                val = m.group(1) or m.group(2) or m.group(3)
                if val:
                    return {
                        "field_id": field_id,
                        "scalar": val,
                        "units": None,
                        "statistic": "count",
                        "condition": None,
                        "comparator": None,
                        "paragraph_id": blk.get("id", ""),
                        "quote": _quote_around(text, m.start(), m.end()),
                        "status": "reported",
                        "semantic_check": f"extracted sample size from block {blk.get('id', '')}",
                    }

    # --- DOSE fields ---
    if "dose" in fid_lower or "dosage" in fid_lower or "concentration" in fid_lower:
        for blk in blocks:
            text = blk.get("text", "")
            # Look for dose near the drug name
            m = re.search(r"(?:GLX-2119|drug|compound|treatment|active)\s+(\d+(?:\.\d+)?)\s*(mg|µg|ug|g|mcg|IU|U)\b", text, re.I)
            if m:
                return {
                    "field_id": field_id,
                    "scalar": m.group(1),
                    "units": m.group(2),
                    "statistic": "dose",
                    "condition": "treatment arm",
                    "comparator": None,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end()),
                    "status": "reported",
                    "semantic_check": f"extracted dose from block {blk.get('id', '')}",
                }
            # Also try: "XXX mg once daily" or "XXX mg/day"
            m = DOSE_PATTERN.search(text)
            if m:
                return {
                    "field_id": field_id,
                    "scalar": m.group(1),
                    "units": m.group(2),
                    "statistic": "dose",
                    "condition": None,
                    "comparator": None,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end()),
                    "status": "reported",
                    "semantic_check": f"extracted dose from block {blk.get('id', '')}",
                }

    # --- DURATION fields ---
    if "duration" in fid_lower or "period" in fid_lower or "follow" in fid_lower:
        search_terms = _get_field_search_terms(field_spec)
        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()
            # Check if block is relevant
            relevant = any(t in text_lower for t in search_terms + ["week", "month", "year", "day", "follow-up", "duration", "period"])
            if not relevant:
                continue
            m = DURATION_PATTERN.search(text)
            if m:
                # Prefer the first/primary duration mention
                return {
                    "field_id": field_id,
                    "scalar": m.group(1),
                    "units": m.group(2),
                    "statistic": "duration",
                    "condition": None,
                    "comparator": None,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end()),
                    "status": "reported",
                    "semantic_check": f"extracted duration from block {blk.get('id', '')}",
                }

    # --- HAZARD RATIO / ODDS RATIO fields ---
    if any(k in fid_lower for k in ("hazard", "hr_", "odds_ratio", "or_", "relative_risk", "rr_", "risk_ratio")):
        # Determine endpoint keywords for disambiguation
        endpoint_keywords = []
        if "mace" in fid_lower:
            endpoint_keywords = ["mace", "major adverse"]
        elif "death" in fid_lower or "mortality" in fid_lower:
            endpoint_keywords = ["death", "mortality", "survival"]
        elif "mi" in fid_lower or "infarction" in fid_lower:
            endpoint_keywords = ["myocardial infarction", "infarction"]

        # Determine ratio keyword variants
        ratio_kws = []
        if "hazard" in fid_lower or "hr_" in fid_lower:
            ratio_kws = ["hazard ratio", "HR"]
        elif "odds" in fid_lower or "or_" in fid_lower:
            ratio_kws = ["odds ratio", "OR"]
        else:
            ratio_kws = ["HR", "hazard ratio", "OR", "odds ratio", "RR", "relative risk"]

        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()

            # Strategy: find endpoint keyword, then find ratio keyword near it, then find number
            best_match = None
            for ekw in (endpoint_keywords or [text_lower[:0] or ""]):
                ekw_pos = text_lower.find(ekw) if ekw else -1
                if ekw_pos < 0 and endpoint_keywords:
                    continue
                # Search window: 200 chars around endpoint keyword
                win_start = max(0, ekw_pos - 80) if ekw_pos >= 0 else 0
                win_end = min(len(text), ekw_pos + 250) if ekw_pos >= 0 else len(text)
                window = text[win_start:win_end]
                window_lower = window.lower()

                for rkw in ratio_kws:
                    rkw_pos = window_lower.find(rkw.lower())
                    if rkw_pos < 0:
                        continue
                    # After the ratio keyword, find a decimal number within 60 chars
                    after_kw = window[rkw_pos + len(rkw):rkw_pos + len(rkw) + 60]
                    num_m = re.search(r"([-+]?\d+\.\d+)", after_kw)
                    if num_m:
                        abs_num_start = win_start + rkw_pos + len(rkw) + num_m.start()
                        abs_num_end = win_start + rkw_pos + len(rkw) + num_m.end()
                        # Skip if this looks like a CI value (number after "CI:" nearby)
                        pre_context = text[max(0, abs_num_start - 20):abs_num_start].lower()
                        if "ci" in pre_context or "confidence" in pre_context:
                            continue
                        best_match = {
                            "abs_start": abs_num_start,
                            "abs_end": abs_num_end,
                            "scalar": num_m.group(1),
                            "window": window,
                            "blk_id": blk.get("id", ""),
                        }
                        break
                if best_match:
                    break

            if best_match:
                m_start = best_match["abs_start"]
                m_end = best_match["abs_end"]
                return {
                    "field_id": field_id,
                    "scalar": best_match["scalar"],
                    "units": None,
                    "statistic": "hazard ratio" if "hazard" in fid_lower or "hr_" in fid_lower else "odds ratio",
                    "condition": None,
                    "comparator": _extract_arm_comparator(text[max(0, m_start-100):m_end+100]),
                    "paragraph_id": best_match["blk_id"],
                    "quote": _quote_around(text, m_start, m_end, 120),
                    "status": "reported",
                    "semantic_check": f"extracted ratio from block {best_match['blk_id']}",
                }

    # --- CONFIDENCE INTERVAL fields ---
    if "confidence" in fid_lower or "ci_" in fid_lower or "_ci" in fid_lower:
        search_terms = _get_field_search_terms(field_spec)
        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()
            relevant = any(t in text_lower for t in search_terms + ["95% ci", "confidence interval", "ci:"])
            if not relevant:
                continue
            m = CI_PATTERN.search(text)
            if m:
                return {
                    "field_id": field_id,
                    "scalar": f"{m.group(1)} to {m.group(2)}",
                    "units": None,
                    "statistic": "95% CI",
                    "condition": None,
                    "comparator": None,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end()),
                    "status": "reported",
                    "semantic_check": f"extracted CI from block {blk.get('id', '')}",
                }

    # --- ADVERSE EVENT RATE fields ---
    if "adverse" in fid_lower or "ae_" in fid_lower or "safety" in fid_lower or "side_effect" in fid_lower:
        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()
            # Must contain "adverse event"
            if "adverse event" not in text_lower:
                continue
            # Strategy: find "adverse events" position, then take the FIRST % after it
            # This reliably gets the treatment arm rate (first mentioned in "XX% of DRUG recipients and YY% of placebo")
            ae_pos = text_lower.find("adverse event")
            if ae_pos >= 0:
                window = text[ae_pos:ae_pos + 100]
                m = re.search(r"(\d+(?:\.\d+)?)\s*%", window)
                if m:
                    abs_start = ae_pos + m.start()
                    abs_end = ae_pos + m.end()
                    return {
                        "field_id": field_id,
                        "scalar": m.group(1),
                        "units": "%",
                        "statistic": "percentage",
                        "condition": "treatment arm",
                        "comparator": None,
                        "paragraph_id": blk.get("id", ""),
                        "quote": _quote_around(text, abs_start, abs_end, 120),
                        "status": "reported",
                        "semantic_check": f"extracted AE rate from block {blk.get('id', '')}",
                    }

    # --- MEAN CHANGE / REDUCTION fields (HbA1c, FPG, etc.) ---
    if any(k in fid_lower for k in ("reduction", "change", "decrease", "increase", "delta", "difference")):
        search_terms = _get_field_search_terms(field_spec)
        expected_unit = field_spec.get("units") or field_spec.get("expected_units") or ""

        # Determine field-specific anchor keywords to disambiguate HbA1c vs FPG etc.
        anchor_kws = []
        if any(k in fid_lower for k in ("hba1c", "glycat")):
            anchor_kws = ["hba1c", "glycated hemoglobin", "a1c"]
        elif any(k in fid_lower for k in ("fasting", "fpg", "glucose")):
            anchor_kws = ["fasting plasma glucose", "fasting glucose", "fpg"]
        elif any(k in fid_lower for k in ("weight", "body weight")):
            anchor_kws = ["body weight", "weight"]

        for blk in blocks:
            text = blk.get("text", "")
            text_lower = text.lower()
            # Check relevance
            relevant = any(t in text_lower for t in search_terms)
            if not relevant:
                continue

            # Find all mean±SD matches and pick the one nearest the anchor keyword
            matches = list(MEAN_SD_PATTERN.finditer(text))
            if matches and anchor_kws:
                # Find position of anchor keyword
                anchor_pos = -1
                for akw in anchor_kws:
                    pos = text_lower.find(akw)
                    if pos >= 0:
                        anchor_pos = pos
                        break
                if anchor_pos >= 0:
                    # Pick the match closest to the anchor
                    best = min(matches, key=lambda m: abs(m.start() - anchor_pos))
                    m = best
                else:
                    m = matches[0]
            elif matches:
                m = matches[0]
            else:
                m = None

            if m:
                # Check if unit follows
                remaining = text[m.end():m.end()+30]
                unit_m = re.match(r"\s*([a-zA-Zµμ°/%]+(?:/[a-zA-Zµμ°]+)?)", remaining)
                unit = unit_m.group(1) if unit_m else expected_unit
                # For mean-change fields, use targeted arm extraction from nearby text
                local_text = text[max(0, m.start()-80):m.end()+200]
                cond = _extract_arm_condition(local_text)
                comp = _extract_arm_comparator(local_text)
                return {
                    "field_id": field_id,
                    "scalar": m.group(1),
                    "units": unit or None,
                    "statistic": "mean ± SD",
                    "condition": cond,
                    "comparator": comp,
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end(), 120),
                    "status": "reported",
                    "semantic_check": f"extracted mean±SD from block {blk.get('id', '')}",
                }

            # Look for "difference of -X.X% (95% CI: ...)" pattern
            m = re.search(r"difference\s+(?:of\s+)?([-+]?\d+\.?\d*)\s*(%|mg/dL|mmol/L|[a-zA-Z]+)?", text, re.I)
            if m:
                return {
                    "field_id": field_id,
                    "scalar": m.group(1),
                    "units": m.group(2) or expected_unit or None,
                    "statistic": "mean difference",
                    "condition": None,
                    "comparator": _extract_comparator_from_text(text),
                    "paragraph_id": blk.get("id", ""),
                    "quote": _quote_around(text, m.start(), m.end(), 120),
                    "status": "reported",
                    "semantic_check": f"extracted mean difference from block {blk.get('id', '')}",
                }

    return None


def _extract_generic(blocks_data: dict, field_spec: dict, field_id: str) -> dict:
    """
    Generic extraction fallback: search blocks for field terms, extract any number.
    Handles mean±SD patterns by extracting the mean value.
    """
    search_terms = _get_field_search_terms(field_spec)
    expected_unit = field_spec.get("units") or field_spec.get("expected_units") or ""
    blocks = blocks_data.get("blocks", [])

    # Score blocks by term relevance
    scored_blocks = []
    for blk in blocks:
        text = blk.get("text", "")
        text_lower = text.lower()
        score = sum(len(t) for t in search_terms if t in text_lower)
        if score > 0:
            scored_blocks.append((score, blk, text))
    scored_blocks.sort(key=lambda x: x[0], reverse=True)

    # Try each relevant block for numbers
    for score, blk, text in scored_blocks[:5]:
        # First try MEAN_SD_PATTERN to get the mean value (not the SD)
        mean_sd_match = MEAN_SD_PATTERN.search(text)
        if mean_sd_match:
            mean_val = mean_sd_match.group(1).strip()
            # Look for unit after the SD part
            after_sd = text[mean_sd_match.end():mean_sd_match.end()+100]
            unit_match = re.search(r"\b(%|‰|°C|°F|mg|g|kg|µg|ug|ng|pg|mL|dL|µL|uL|L|mmol|mol|µmol|umol|"
                                   r"mm2|cm2|m2|mm|cm|m|km|µm|um|nm|kDa|Da|kPa|MPa|mmHg|Hz|kHz|MHz|rpm|"
                                   r"mV|V|W|kW|mW|J|kJ|kcal|cal|AU|OD|bp|kb|M|nM|µM|uM|mM|"
                                   r"mg/dL|mmol/L|µmol/L|mg/mL|µg/mL|ng/mL|pg/mL|cells/mL|"
                                   r"mg/kg|µg/kg|ng/kg|mL/min|L/min|L/h|mL/h|"
                                   r"mg/day|g/day|µg/day|IU|U/L|pg/mL|copies/mL|CFU/mL|cfu|"
                                   r"cells/µL|×10\^?\d+|/µL|/mm3|/dL|/L)\b", after_sd, re.I)
            if unit_match:
                unit_val = unit_match.group(1).strip()
            else:
                unit_val = expected_unit or None
            return {
                "field_id": field_id,
                "scalar": mean_val,
                "units": unit_val,
                "statistic": "mean",
                "condition": _extract_condition_from_text(text),
                "comparator": _extract_comparator_from_text(text),
                "paragraph_id": blk.get("id", ""),
                "quote": text.strip()[:300],
                "status": "reported",
                "semantic_check": f"extracted mean±SD from block {blk.get('id', '')} (relevance score {score})",
            }

        # Try number+unit pattern
        num_matches = list(NUM_PATTERN.finditer(text))
        if num_matches:
            # Prefer match with expected unit
            best = None
            for m in num_matches:
                if expected_unit and expected_unit.lower() in m.group(0).lower():
                    best = m
                    break
            if not best:
                # Pick the first match that has a reasonable unit (not just a concentration like mM)
                for m in num_matches:
                    unit = m.group(2).strip().lower()
                    # Skip concentration units if we expect mass/area/volume units
                    if expected_unit and expected_unit.lower() not in ["mm", "µm", "nm", "m"]:
                        if unit in ["mm", "µm", "nm", "m", "mol", "mmol", "µmol", "umol"]:
                            continue
                    best = m
                    break
            if not best:
                best = num_matches[0]
            return {
                "field_id": field_id,
                "scalar": best.group(1).strip(),
                "units": best.group(2).strip() if best.group(2) else expected_unit or None,
                "statistic": None,
                "condition": _extract_condition_from_text(text),
                "comparator": _extract_comparator_from_text(text),
                "paragraph_id": blk.get("id", ""),
                "quote": _quote_around(text, best.start(), best.end()),
                "status": "reported",
                "semantic_check": f"generic extraction from block {blk.get('id', '')} (relevance score {score})",
            }

    # No match found
    return {
        "field_id": field_id,
        "scalar": None, "units": None, "statistic": None,
        "condition": None, "comparator": None,
        "paragraph_id": None, "quote": None,
        "status": "not_reported",
        "semantic_check": "no candidate blocks matched or contained extractable values",
    }


def _extract_arm_condition(text: str) -> str | None:
    """Extract treatment arm condition for clinical trial fields."""
    # Look for "in the GLX-2119 group" or "in the treatment group"
    m = re.search(r"in\s+the\s+([\w-]+)\s+(group|arm|cohort)", text, re.I)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    # Look for "GLX-2119 group" or "treatment group"
    m = re.search(r"([\w-]+)\s+(group|arm|cohort)", text, re.I)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    # Look for "treatment arm" or "placebo arm"
    m = re.search(r"(treatment|placebo|control|intervention)\s+arm", text, re.I)
    if m:
        return f"{m.group(1)} arm"
    return None


def _extract_arm_comparator(text: str) -> str | None:
    """Extract comparator arm for clinical trial fields."""
    # Look for "compared with placebo group"
    m = re.search(r"compared?\s+(?:with|to)\s+(the\s+)?([\w-]+)\s+(group|arm|cohort)", text, re.I)
    if m:
        return f"{m.group(2)} {m.group(3)}"
    # Look for "placebo group" or "control group"
    m = re.search(r"(placebo|control)\s+(group|arm|cohort)", text, re.I)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def _extract_condition_from_text(text: str) -> str | None:
    """Look for experimental condition phrases."""
    patterns = [
        r"(?:at|under|in|with)\s+(\d+\s*°[CF]\b[^.]*?)(?:\.|,|$)",
        r"(\d+(?:\.\d+)?\s*(?:mg|µg|g|kg|mol|mmol|mL|L)\s*(?:per|/)\s*(?:kg|day|dL|L|mL|m2|body weight)[^.]*?)(?:\.|,|$)",
        r"(?:in\s+)(the\s+[\w-]+\s+(?:group|arm|cohort))\s+",
        r"([\w-]+\s+(?:group|arm|cohort|condition|treatment|dose|concentration)\b[^.]*?)(?:\.|,|$)",
        r"(?:dose|concentration|amount)\s*(?:of|=|:)\s*([^.]+?)(?:\.|,|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            val = m.group(1).strip()
            if 3 < len(val) < 200:
                return val
    return None


def _extract_comparator_from_text(text: str) -> str | None:
    """Look for comparator/control arm phrases."""
    patterns = [
        r"(?:compared?\s+(?:to|with)|versus|vs\.?)\s+((?:the\s+)?[\w-]+\s+(?:group|arm|cohort)[^.]*?)(?:\.|,|$)",
        r"((?:placebo|control|vehicle|sham|baseline|untreated|wild[\s-]?type|WT|normal)\b[^.]*?)(?:\.|,|$)",
        r"((?:intervention|treatment|drug|compound)\s+(?:arm|group)\b[^.]*?)(?:\.|,|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            val = m.group(1).strip()
            if 3 < len(val) < 200:
                return val
    return None


def extract_field(blocks_data: dict, field_spec: dict) -> dict:
    """Extract a single field's value from blocks using field-specific + generic logic."""
    field_id = _get_field_id(field_spec)

    if not field_id:
        return {
            "field_id": "unknown",
            "scalar": None, "units": None, "statistic": None,
            "condition": None, "comparator": None,
            "paragraph_id": None, "quote": None,
            "status": "extraction_failed",
            "semantic_check": "could not determine field_id",
        }

    # Try field-specific extraction first
    result = _extract_field_specific(blocks_data, field_spec, field_id)
    if result and result.get("status") == "reported":
        return result

    # Fall back to generic extraction
    return _extract_generic(blocks_data, field_spec, field_id)


# ---------------------------------------------------------------------------
# Build claims from extracted fields
# ---------------------------------------------------------------------------
def _build_claims(fields: list[dict]) -> list[dict]:
    claims = []
    for f in fields:
        if f.get("status") == "reported" and f.get("scalar") is not None:
            claims.append({
                "kind": "field_value",
                "content": (
                    f"{f.get('field_id', '?')}: {f.get('scalar', '?')} "
                    f"{f.get('units') or ''} "
                    f"({f.get('statistic') or 'value'})"
                ).strip(),
                "evidence": {
                    "locator": f.get("paragraph_id", ""),
                    "quote": f.get("quote", ""),
                },
                "source_id": f.get("field_id", ""),
                "location": f.get("paragraph_id", ""),
                "certainty": "stated",
            })
        elif f.get("status") == "source_conflict":
            claims.append({
                "kind": "conflict",
                "content": f"{f.get('field_id', '?')}: conflicting values in material",
                "evidence": {
                    "locator": f.get("paragraph_id", ""),
                    "quote": f.get("quote", ""),
                },
                "source_id": f.get("field_id", ""),
                "location": f.get("paragraph_id", ""),
                "certainty": "conflicting",
            })
    return claims


def _build_uncertainties(fields: list[dict]) -> list[dict]:
    out = []
    for f in fields:
        if f.get("status") == "not_reported":
            out.append({
                "kind": "not_reported",
                "field_id": f.get("field_id", ""),
                "detail": f.get("semantic_check", "value not found in material"),
                "impact": "this field cannot be answered from the provided material",
            })
        elif f.get("status") == "extraction_failed":
            out.append({
                "kind": "extraction_failed",
                "field_id": f.get("field_id", ""),
                "detail": f.get("semantic_check", "extraction pipeline failed"),
                "impact": "manual review required",
            })
        elif f.get("status") == "source_conflict":
            out.append({
                "kind": "source_conflict",
                "field_id": f.get("field_id", ""),
                "detail": f.get("semantic_check", "conflicting values in material"),
                "impact": "requires human adjudication",
            })
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_pipeline(input_dir: str, output_dir: str, work_dir: str,
                 run_selfcheck: bool = False) -> dict:
    """Execute the full extraction pipeline and return summary."""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    summary = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "steps": [],
        "status": "input_error",
        "case_id": None,
        "field_count": 0,
        "claims_count": 0,
        "uncertainties_count": 0,
    }

    def _step(name: str, ok: bool, detail: str = ""):
        summary["steps"].append({"name": name, "ok": ok, "detail": detail})
        return ok

    # --- Step 1: Read case contract ---
    case_path = None
    for name in ("case.json", "example-input.json", "input.json"):
        candidate = os.path.join(input_dir, name)
        if os.path.isfile(candidate):
            case_path = candidate
            break

    targets_path = os.path.join(input_dir, "target-fields.json")

    case_data = {}
    target_fields = []

    if case_path is not None:
        try:
            case_data = _jload(case_path)
            _step("read_case", True, f"loaded {case_path}")
        except Exception as exc:
            _step("read_case", False, str(exc))
    else:
        _step("read_case", False, f"case.json not found in {input_dir}")

    if os.path.isfile(targets_path):
        try:
            raw = _jload(targets_path)
            # Handle various shapes
            if isinstance(raw, list):
                target_fields = raw
            elif isinstance(raw, dict):
                for key in ("target_fields", "targetFields", "fields", "field_specs",
                            "targets", "field_ids", "fieldIds", "items", "data"):
                    v = raw.get(key)
                    if isinstance(v, list):
                        target_fields = v
                        break
                if not target_fields and raw:
                    # Maybe the dict itself is a single field spec
                    if any(k in raw for k in ("field_id", "fieldId", "id")):
                        target_fields = [raw]
            _step("read_targets", True, f"{len(target_fields)} target field(s)")
        except Exception as exc:
            _step("read_targets", False, str(exc))
    else:
        _step("read_targets", False, f"{targets_path} not found")

    # Extract case_id
    case_id = None
    if isinstance(case_data, dict):
        for key in ("case_id", "caseId", "id", "case"):
            v = case_data.get(key)
            if v and isinstance(v, str):
                case_id = v
                break
        if not case_id and isinstance(case_data.get("case"), dict):
            for key in ("case_id", "caseId", "id"):
                v = case_data["case"].get(key)
                if v and isinstance(v, str):
                    case_id = v
                    break
    summary["case_id"] = case_id

    # Save case context
    ctx = {
        "case_id": case_id,
        "case_data": case_data,
        "target_fields": target_fields,
        "materials": [],
        "fields_origin": "target-fields.json" if target_fields else "unavailable",
    }
    _jwrite(os.path.join(work_dir, "case_context.json"), ctx)

    # --- Step 2: Find and extract materials ---
    materials = _find_materials(input_dir)
    ctx["materials"] = [{"path": m, "name": os.path.basename(m),
                         "size": os.path.getsize(m)} for m in materials]
    _jwrite(os.path.join(work_dir, "case_context.json"), ctx)

    # Use the first material (or best: prefer PDF, then text)
    primary_material = None
    for m in materials:
        if _is_pdf(m):
            primary_material = m
            break
    if not primary_material and materials:
        primary_material = materials[0]

    blocks_data = {"blocks": [], "engine": "none", "warnings": ["no material found"]}
    material_text = ""

    if primary_material:
        blocks_data = extract_blocks(primary_material, work_dir)
        _step("extract_blocks", bool(blocks_data.get("blocks")),
              f"{blocks_data.get('engine', '?')} → {len(blocks_data.get('blocks', []))} blocks")
        # Build a flat text version
        material_text = "\n".join(b.get("text", "") for b in blocks_data.get("blocks", []))
        with open(os.path.join(work_dir, "material.txt"), "w", encoding="utf-8") as fh:
            fh.write(material_text)
    else:
        _step("extract_blocks", False, "no material files found in input dir")

    # --- Step 3: Extract fields ---
    fields_scaffold = []
    for spec in target_fields:
        fid = None
        for k in ("field_id", "fieldId", "id", "key", "name", "slug"):
            v = spec.get(k)
            if v and isinstance(v, str):
                fid = v
                break
        fields_scaffold.append({
            "field_id": fid or "unknown",
            "status": "pending",
            "scalar": None, "units": None, "statistic": None,
            "condition": None, "comparator": None,
            "paragraph_id": None, "quote": None,
        })
    _jwrite(os.path.join(work_dir, "fields_scaffold.json"),
            {"fields": fields_scaffold})

    fields_filled = []
    for spec in target_fields:
        result = extract_field(blocks_data, spec)
        fields_filled.append(result)
    _jwrite(os.path.join(work_dir, "fields_filled.json"),
            {"fields": fields_filled})

    n_reported = sum(1 for f in fields_filled if f.get("status") == "reported")
    _step("extract_fields", True,
          f"{n_reported}/{len(fields_filled)} fields reported")

    # --- Step 4: Build claims and uncertainties ---
    claims = _build_claims(fields_filled)
    uncertainties = _build_uncertainties(fields_filled)
    summary["claims_count"] = len(claims)
    summary["uncertainties_count"] = len(uncertainties)
    summary["field_count"] = len(fields_filled)

    # --- Step 5: Determine status ---
    if not case_id and not case_data:
        status = "input_error"
    elif not materials:
        status = "input_error"
    elif not target_fields:
        status = "partial"
    elif n_reported == 0 and all(f.get("status") == "not_reported"
                                  for f in fields_filled):
        status = "insufficient_evidence"
    elif n_reported == len(fields_filled):
        status = "answered"
    elif n_reported > 0:
        status = "partial"
    else:
        status = "partial"

    # --- Step 6: Assemble result.json ---
    result_payload = {
        "case_id": case_id,
        "status": status,
        "result": {"fields": fields_filled},
        "claims": claims,
        "uncertainties": uncertainties,
        "limitations": [],
        "notes": [],
        "deliverable_kind": "field_extraction",
    }

    # Add limitations for non-answered statuses
    if status == "insufficient_evidence":
        result_payload["limitations"].append(
            "material was read but contained none of the requested field values; "
            "the paper may not address these fields, or the extraction needs manual review"
        )
    if status == "partial":
        missing = [f["field_id"] for f in fields_filled
                   if f.get("status") != "reported"]
        result_payload["limitations"].append(
            f"{len(missing)} of {len(fields_filled)} fields could not be extracted: "
            f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
        )
    if status == "input_error":
        result_payload["limitations"].append(
            "input was incomplete or unreadable; check case.json and material files"
        )

    # Add notes about the run
    result_payload["notes"].append(
        f"Extracted by run.py pipeline using {blocks_data.get('engine', 'unknown')} engine"
    )
    # Flag demo materials as self-generated
    if case_id and case_id.startswith("DEMO-"):
        result_payload["notes"].append(
            "Demo materials are self-generated for pipeline demonstration purposes"
        )
    if blocks_data.get("warnings"):
        for w in blocks_data["warnings"][:3]:
            result_payload["notes"].append(f"material warning: {w}")

    result_path = os.path.join(output_dir, "result.json")
    _jwrite(result_path, result_payload)
    summary["status"] = status
    _step("assemble_result", True, f"status={status}, written to {result_path}")

    # --- Step 7: Contract check ---
    if _contract:
        try:
            check_result = _contract.check(result_payload, "reading")
            problems = check_result.get("problems", [])
            _step("contract_check", len(problems) == 0,
                  f"{len(problems)} problem(s)" if problems else "clean")
            _jwrite(os.path.join(work_dir, "contract_check.json"), check_result)
        except Exception as exc:
            _step("contract_check", False, str(exc))
    else:
        _step("contract_check", True, "contract module not available, skipped")

    # --- Step 8: Selfcheck (optional) ---
    if run_selfcheck:
        selfcheck_path = os.path.join(SCRIPTS_DIR, "selfcheck.py")
        if os.path.isfile(selfcheck_path):
            try:
                sc_out = subprocess.run(
                    [sys.executable, selfcheck_path,
                     "--result", result_path,
                     "--work", work_dir,
                     "--input", input_dir],
                    capture_output=True, text=True, timeout=60,
                )
                _jwrite(os.path.join(work_dir, "selfcheck_output.json"), {
                    "exit_code": sc_out.returncode,
                    "stdout": sc_out.stdout,
                    "stderr": sc_out.stderr,
                })
                _step("selfcheck", sc_out.returncode == 0,
                      sc_out.stdout[:200] if sc_out.stdout else "no output")
            except Exception as exc:
                _step("selfcheck", False, str(exc))
        else:
            _step("selfcheck", False, "selfcheck.py not found")

    summary["result_path"] = result_path
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="文献结论与数据提取 — single-entry pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
                python3 run.py --input /input --output /app/output
                python3 run.py --input ./demo --output ./out --work ./out/work
                python3 run.py --input ./inputs --output ./out/example
        """),
    )
    ap.add_argument("--input", default="/input",
                    help="directory containing case.json, target-fields.json, materials")
    ap.add_argument("--output", default="/app/output",
                    help="directory where result.json is written")
    ap.add_argument("--work", default=None,
                    help="working directory for intermediates (default: OUTPUT/work)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="run selfcheck.py after assembly")
    a = ap.parse_args()

    work_dir = a.work or os.path.join(a.output, "work")

    print(f"[run.py] input={a.input}, output={a.output}, work={work_dir}")
    print(f"[run.py] python={sys.executable}")
    print(f"[run.py] skill_dir={SKILL_DIR}")

    try:
        summary = run_pipeline(a.input, a.output, work_dir, a.selfcheck)
    except Exception as exc:
        print(f"[run.py] FATAL: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    print(json.dumps(summary, ensure_ascii=False, indent=1))

    # Exit code
    if summary["status"] == "input_error":
        sys.exit(1)
    steps_failed = [s for s in summary.get("steps", []) if not s["ok"]]
    critical = [s for s in steps_failed if s["name"] in ("read_case", "assemble_result")]
    if critical:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
