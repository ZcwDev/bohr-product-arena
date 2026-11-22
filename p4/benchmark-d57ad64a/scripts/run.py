#!/usr/bin/env python3
"""Single-entry-point for the Academic Writing Benchmark & Evaluation Tool.

Usage:
    python3 run.py --input DIR --output DIR

Reads case data and materials from --input, performs benchmark evaluation
(item derivation if needed, scoring, rater agreement, failure taxonomy,
improvement suggestions), and writes result.json + report + artifacts to --output.

Defaults: --input /input  --output /app/output
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

# --- evaluator-environment bootstrap ---
# The science stack (fitz / matplotlib / python-docx / python-pptx / playwright
# / CairoSVG) lives in a dedicated venv; re-exec under it when available.
import os as _os, sys as _sys

_TOOLS_PY = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
if (_os.path.exists(_TOOLS_PY) and not _os.environ.get("_SKILL_REEXEC")
        and _os.path.realpath(_sys.executable) != _os.path.realpath(_TOOLS_PY)):
    _os.environ["_SKILL_REEXEC"] = "1"
    try:
        _os.execv(_TOOLS_PY, [_TOOLS_PY, _os.path.abspath(__file__)] + _sys.argv[1:])
    except OSError:
        pass
# --- end bootstrap ---

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zlib
from pathlib import Path

T0 = time.time()

def log(msg: str):
    print(f"[run +{time.time()-T0:5.1f}s] {msg}", file=sys.stderr)

# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
SKIP_NAMES = {"result.json", "per_item.tsv", "report.md", "selfcheck.json",
              "run.md", "skill.md", "gain.md", "rubric.md", "compliance.md"}

FAMILIES = {
    "items":      ("items", "tasks", "questions"),
    "candidates": ("answers", "submissions", "drafts"),
    "references": ("reference", "gold", "expected"),
    "raters":     ("rater", "annot", "judge"),
}

def _match_family(name: str) -> str | None:
    stem = Path(name).stem.lower()
    for fam, patterns in FAMILIES.items():
        for p in patterns:
            if p in stem:
                return fam
    return None

def discover_files(input_dir: str) -> dict:
    """Discover input files by filename family."""
    found = {"case": None, "items": None, "candidates": None,
             "references": None, "raters": None, "materials": []}
    if not os.path.isdir(input_dir):
        return found
    for entry in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, entry)
        if not os.path.isfile(full):
            continue
        low = entry.lower()
        if low in SKIP_NAMES:
            continue
        if "case" in low and low.endswith(".json") and found["case"] is None:
            found["case"] = full
            continue
        fam = _match_family(entry)
        if fam and low.endswith(".json"):
            found[fam] = full
            continue
        ext = Path(entry).suffix.lower()
        if ext in (".pdf", ".txt", ".md", ".tex", ".bib", ".docx", ".csv"):
            found["materials"].append(full)
    return found

# ---------------------------------------------------------------------------
# PDF text extraction (stdlib fallback + optional fitz / pdftotext)
# ---------------------------------------------------------------------------
def _pure_pdf_text(path: str) -> list[str]:
    """Extract text from PDF using only stdlib (zlib + regex)."""
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        parts: list[str] = []
        in_array = False
        for t in re.findall(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?", text):
            if t == "[":
                in_array = True
            elif t == "]":
                in_array = False
            elif t.startswith("(") and in_array:
                s = t[1:-1].encode("latin-1").decode("unicode_escape", "replace")
                parts.append(s)
        if parts:
            chunks.append("".join(parts))
    return chunks

def extract_pdf_text(path: str) -> list[str]:
    """Extract text from PDF, trying fitz first, then pdftotext, then pure stdlib."""
    try:
        import fitz
        doc = fitz.open(path)
        pages = [page.get_text() for page in doc]
        doc.close()
        if any(p.strip() for p in pages):
            log(f"PDF text via fitz: {len(pages)} pages from {path}")
            return pages
    except Exception:
        pass
    # pdftotext (poppler)
    try:
        import subprocess
        r = subprocess.run(["pdftotext", "-layout", path, "-"],
                          capture_output=True, timeout=30)
        if r.returncode == 0:
            text = r.stdout.decode("utf-8", "replace")
            pages = re.split(r"\f", text)
            if any(p.strip() for p in pages):
                log(f"PDF text via pdftotext: {len(pages)} pages from {path}")
                return pages
    except Exception:
        pass
    pages = _pure_pdf_text(path)
    log(f"PDF text via stdlib: {len(pages)} chunks from {path}")
    return pages

def read_text_file(path: str) -> str:
    """Read a text-based file."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "\n".join(extract_pdf_text(path))
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            pass
        # stdlib fallback: extract from docx zip
        import zipfile
        try:
            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", xml)
        except Exception:
            return ""
    try:
        return open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Load JSON inputs
# ---------------------------------------------------------------------------
def load_json(path: str | None) -> list | dict | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Failed to load {path}: {e}")
        return None

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return "unknown"
    return h.hexdigest()[:16]

# ---------------------------------------------------------------------------
# Derive items from materials (when no explicit item set is provided)
# ---------------------------------------------------------------------------
def derive_items(materials_text: str, max_items: int = 8) -> list[dict]:
    """Derive benchmark items from source material text.

    Each factual/numerical statement becomes a writing item with
    observable criteria (accuracy, completeness, structure).
    """
    sentences = re.split(r'(?<=[.!?])\s+', materials_text)
    factual = []
    patterns = [
        r'\d+\.?\d*\s*(%|percent|million|billion|thousand|kg|g|mg|ml|l|km|m|cm|mm|°C|°F|Hz|kHz|MHz|GHz)',
        r'\d{4}',  # years
        r'(p\s*[<>=]\s*0\.\d+)',  # p-values
        r'(r\s*=\s*-?\d+\.\d+)',  # correlations
        r'(\d+\.?\d*\s*±\s*\d+\.?\d*)',  # mean ± sd
    ]
    for s in sentences:
        s = s.strip()
        if len(s) < 20 or len(s) > 500:
            continue
        has_fact = any(re.search(p, s) for p in patterns)
        has_entity = bool(re.search(r'[A-Z][a-z]+\s+[A-Z]', s))
        if has_fact or has_entity:
            factual.append(s)
        if len(factual) >= max_items * 2:
            break

    items = []
    seen = set()
    for i, sent in enumerate(factual[:max_items]):
        key = sent[:50]
        if key in seen:
            continue
        seen.add(key)
        item_id = f"derived-{i+1:02d}"
        items.append({
            "item_id": item_id,
            "prompt": f"Rewrite the following factual statement accurately, "
                      f"preserving all numbers, units, and proper names: \"{sent}\"",
            "criteria": [
                {"name": "accuracy", "weight": 2,
                 "description": "All numbers, units, and factual claims match the source"},
                {"name": "completeness", "weight": 1,
                 "description": "No key information omitted"},
                {"name": "structure", "weight": 1,
                 "description": "Grammatically correct and logically organized"},
            ],
            "required_sections": [],
            "source_sentence": sent,
        })
    return items

# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------
def score_item(item: dict, candidate: dict | None, reference: dict | None) -> dict:
    """Score a single benchmark item against reference and candidate."""
    item_id = item.get("item_id", "unknown")
    criteria = item.get("criteria", [])
    max_score = sum(c.get("weight", 1) for c in criteria)

    if candidate is None or not candidate.get("answer", "").strip():
        return {
            "item_id": item_id,
            "prompt": item.get("prompt", ""),
            "verdict": "missing_output" if candidate is None else "empty_output",
            "score": 0,
            "max_score": max_score,
            "failure": "missing_output" if candidate is None else "empty_output",
            "criteria": [{"criterion": c["name"], "score": 0, "weight": c.get("weight", 1),
                          "why": "No candidate answer provided" if candidate is None else "Candidate answer is empty"}
                         for c in criteria],
            "evidence": {"source": "none", "locator": "N/A"},
        }

    answer = candidate.get("answer", "")
    ref_accept = reference.get("accept", []) if reference else []
    ref_reject = reference.get("reject", []) if reference else []
    source_sent = item.get("source_sentence", "")

    criterion_results = []
    total = 0
    failure = None

    for c in criteria:
        name = c["name"]
        weight = c.get("weight", 1)
        score_val = 0
        why = ""

        if name == "accuracy":
            # Check for reject patterns (substantive errors)
            reject_hit = False
            for r in ref_reject:
                if r.lower() in answer.lower():
                    reject_hit = True
                    why = f"Contains reject-pattern: '{r}'"
                    break
            if reject_hit:
                score_val = 0
                failure = "wrong_fact"
            else:
                # Check accept patterns
                accept_hits = sum(1 for a in ref_accept if a.lower() in answer.lower())
                if ref_accept:
                    ratio = accept_hits / len(ref_accept)
                    score_val = weight if ratio >= 0.5 else 0
                    why = f"Accept-pattern match: {accept_hits}/{len(ref_accept)}"
                elif source_sent:
                    # Check key numbers from source are preserved
                    nums = re.findall(r'\d+\.?\d*', source_sent)
                    preserved = sum(1 for n in nums if n in answer)
                    if nums:
                        ratio = preserved / len(nums)
                        score_val = weight if ratio >= 0.8 else (weight // 2 if ratio >= 0.5 else 0)
                        why = f"Number preservation: {preserved}/{len(nums)}"
                    else:
                        score_val = weight
                        why = "No numerical facts to verify"
                else:
                    score_val = weight
                    why = "No reference or source to check against"

        elif name == "completeness":
            # Check if key content is present
            if source_sent:
                words = set(w.lower() for w in re.findall(r'\w{4,}', source_sent))
                answer_words = set(w.lower() for w in re.findall(r'\w{4,}', answer))
                overlap = len(words & answer_words)
                ratio = overlap / max(len(words), 1)
                score_val = weight if ratio >= 0.4 else (weight // 2 if ratio >= 0.2 else 0)
                why = f"Content word overlap: {overlap}/{len(words)} ({ratio:.0%})"
            else:
                score_val = weight if len(answer) > 50 else 0
                why = f"Answer length: {len(answer)} chars"

        elif name == "structure":
            has_sections = bool(re.search(r'^#+\s', answer, re.M))
            has_paragraphs = len(answer.split("\n\n")) >= 2
            grammatical = len(answer) > 30 and answer.strip()[-1] in ".!?"
            checks = sum([has_sections, has_paragraphs, grammatical])
            score_val = weight if checks >= 2 else (weight // 2 if checks >= 1 else 0)
            why = f"Structure checks: sections={has_sections}, paragraphs={has_paragraphs}, grammatical={grammatical}"

        else:
            score_val = weight
            why = f"Criterion '{name}' not implemented; awarded full weight"

        criterion_results.append({
            "criterion": name,
            "score": score_val,
            "weight": weight,
            "why": why,
        })
        total += score_val

    if failure is None:
        if total >= max_score * 0.8:
            failure = None  # pass
        elif total >= max_score * 0.4:
            failure = "style_or_variant"
        else:
            failure = "wrong_fact"

    verdict = "pass" if total >= max_score * 0.8 else ("partial" if total >= max_score * 0.4 else "fail")

    return {
        "item_id": item_id,
        "prompt": item.get("prompt", ""),
        "verdict": verdict,
        "score": total,
        "max_score": max_score,
        "failure": failure,
        "criteria": criterion_results,
        "evidence": {
            "source": "candidate" if candidate else "none",
            "locator": f"answer[:100]={answer[:100]}",
            "source_sentence": source_sent[:200] if source_sent else None,
        },
    }

# ---------------------------------------------------------------------------
# Rater agreement
# ---------------------------------------------------------------------------
def compute_agreement(raters_data: list | None) -> dict:
    """Compute percent agreement and Cohen's kappa from rater data."""
    if not raters_data or not isinstance(raters_data, list):
        return {}

    # Group by rater
    by_rater: dict[str, dict] = {}
    for row in raters_data:
        rater = row.get("rater", "unknown")
        item_id = row.get("item_id", "")
        scores = row.get("scores", {})
        if rater not in by_rater:
            by_rater[rater] = {}
        by_rater[rater][item_id] = scores

    raters = list(by_rater.keys())
    if len(raters) < 2:
        return {"_note": "Fewer than 2 raters; kappa not computed",
                "agreement_computed": False}

    # Use first two raters for kappa
    r1, r2 = raters[0], raters[1]
    common_items = set(by_rater[r1].keys()) & set(by_rater[r2].keys())
    if not common_items:
        return {"_note": "No common items between raters", "agreement_computed": False}

    # Gather all criteria
    all_criteria = set()
    for item_id in common_items:
        all_criteria.update(by_rater[r1].get(item_id, {}).keys())
        all_criteria.update(by_rater[r2].get(item_id, {}).keys())

    result = {}
    for crit in sorted(all_criteria):
        pairs = []
        for item_id in sorted(common_items):
            v1 = by_rater[r1].get(item_id, {}).get(crit)
            v2 = by_rater[r2].get(item_id, {}).get(crit)
            if v1 is not None and v2 is not None:
                # Binarize: 0 vs 1
                b1 = 1 if v1 >= 1 else 0
                b2 = 1 if v2 >= 1 else 0
                pairs.append((b1, b2))

        if not pairs:
            continue
        n = len(pairs)
        agree = sum(1 for a, b in pairs if a == b)
        pct = agree / n

        # Cohen's kappa
        p1_yes = sum(1 for a, _ in pairs if a == 1) / n
        p1_no = 1 - p1_yes
        p2_yes = sum(1 for _, b in pairs if b == 1) / n
        p2_no = 1 - p2_yes
        pe = p1_yes * p2_yes + p1_no * p2_no
        if pe < 1.0:
            kappa = (pct - pe) / (1 - pe)
        else:
            kappa = 1.0 if pct == 1.0 else 0.0

        result[crit] = {
            "n": n,
            "percent_agreement": round(pct, 3),
            "cohen_kappa": round(kappa, 3),
        }

    result["agreement_computed"] = True
    return result

# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------
FAILURE_TYPES = ["wrong_fact", "wrong_format", "style_or_variant",
                 "missing_output", "empty_output"]

def build_failure_taxonomy(scored_items: list) -> dict:
    taxonomy = {ft: [] for ft in FAILURE_TYPES}
    for item in scored_items:
        f = item.get("failure")
        if f and f in taxonomy:
            taxonomy[f].append(item["item_id"])
    return taxonomy

# ---------------------------------------------------------------------------
# Improvement suggestions
# ---------------------------------------------------------------------------
def suggest_improvements(scored_items: list, agreement: dict) -> list[str]:
    suggestions = []
    taxonomy = build_failure_taxonomy(scored_items)

    if taxonomy["wrong_fact"]:
        suggestions.append(
            f"Factual errors in items {taxonomy['wrong_fact']}: "
            f"revise reference answers to include explicit accept/reject patterns for common misconceptions.")

    if taxonomy["wrong_format"]:
        suggestions.append(
            f"Format errors in items {taxonomy['wrong_format']}: "
            f"clarify required_sections in item definitions.")

    if taxonomy["style_or_variant"]:
        suggestions.append(
            f"Style/variant cases in items {taxonomy['style_or_variant']}: "
            f"expand accept list to cover legitimate rephrasing.")

    if taxonomy["missing_output"]:
        suggestions.append(
            f"Missing output in items {taxonomy['missing_output']}: "
            f"check candidate generation pipeline.")

    # Agreement-based suggestions
    for crit, data in agreement.items():
        if crit.startswith("_") or crit == "agreement_computed":
            continue
        kappa = data.get("cohen_kappa")
        if kappa is not None and kappa < 0.4:
            suggestions.append(
                f"Low rater agreement on '{crit}' (κ={kappa}): "
                f"anchor definition is likely ambiguous — revise criterion wording, "
                f"do NOT adjust scores to match.")

    if not suggestions:
        suggestions.append("All items pass with good agreement; no immediate revisions needed.")

    return suggestions

# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(case_id: str, scored_items: list, aggregates: dict,
                   taxonomy: dict, agreement: dict, suggestions: list,
                   uncertainties: list) -> str:
    lines = [
        f"# Academic Writing Benchmark Evaluation Report",
        f"",
        f"**Case ID**: {case_id}",
        f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"",
        f"## Summary",
        f"",
        f"- Items evaluated: {aggregates['n_items']}",
        f"- Total score: {aggregates['total_score']}/{aggregates['max_total']}",
        f"- Mean score: {aggregates['mean_score']:.2f}",
        f"- Pass rate: {aggregates['pass_rate']:.1%}",
        f"",
        f"## Per-Item Results",
        f"",
    ]

    for r in scored_items:
        status_emoji = "✅" if r["verdict"] == "pass" else ("⚠️" if r["verdict"] == "partial" else "❌")
        lines.append(f"### {status_emoji} {r['item_id']}: {r['score']}/{r['max_score']} ({r['verdict']})")
        lines.append(f"")
        lines.append(f"**Prompt**: {r['prompt'][:200]}")
        if r.get("failure"):
            lines.append(f"- **Failure type**: {r['failure']}")
        for c in r.get("criteria", []):
            lines.append(f"- **{c['criterion']}**: {c['score']}/{c['weight']} — {c['why']}")
        if r.get("evidence", {}).get("source_sentence"):
            lines.append(f"- **Source**: \"{r['evidence']['source_sentence'][:150]}\"")
        lines.append("")

    lines += ["## Failure Taxonomy", ""]
    for ft, items in taxonomy.items():
        lines.append(f"- **{ft}**: {items if items else '(none)'}")
    lines.append("")

    if agreement and agreement.get("agreement_computed"):
        lines += ["## Rater Agreement", ""]
        for crit, data in agreement.items():
            if crit.startswith("_") or crit == "agreement_computed":
                continue
            lines.append(f"- **{crit}**: n={data['n']}, agreement={data['percent_agreement']:.1%}, κ={data['cohen_kappa']:.3f}")
        lines.append("")

    lines += ["## Improvement Suggestions", ""]
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")

    if uncertainties:
        lines += ["## Uncertainties & Limitations", ""]
        for u in uncertainties:
            lines.append(f"- {u}")
        lines.append("")

    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Per-item TSV
# ---------------------------------------------------------------------------
def write_per_item_tsv(scored_items: list, path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write("item_id\tverdict\tscore\tmax_score\tfailure\tcriteria_summary\n")
        for r in scored_items:
            crit_summary = "; ".join(
                f"{c['criterion']}={c['score']}/{c['weight']}" for c in r.get("criteria", []))
            f.write(f"{r['item_id']}\t{r['verdict']}\t{r['score']}\t{r['max_score']}\t"
                    f"{r.get('failure', '')}\t{crit_summary}\n")

# ---------------------------------------------------------------------------
# Preserved facts extraction
# ---------------------------------------------------------------------------
def extract_preserved_facts(scored_items: list, materials_text: str) -> list:
    facts = []
    for item in scored_items:
        src = item.get("evidence", {}).get("source_sentence", "")
        if not src:
            continue
        nums = re.findall(r'\d+\.?\d*\s*(?:%|percent|million|billion|thousand|kg|g|mg|ml|l|km|m|cm|mm|°C|°F|Hz|kHz|MHz|GHz|±\s*\d+\.?\d*)', src)
        for n in nums:
            facts.append({
                "value": n.strip(),
                "context": src[:200],
                "source": "source_material",
                "item_id": item["item_id"],
            })
        # Also preserve proper nouns
        nouns = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', src)
        for n in nouns[:3]:
            facts.append({
                "value": n.strip(),
                "context": src[:200],
                "source": "source_material",
                "item_id": item["item_id"],
            })
    return facts

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_pipeline(input_dir: str, output_dir: str) -> int:
    log(f"input_dir={input_dir} output_dir={output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # 1. Discover files
    files = discover_files(input_dir)
    log(f"Discovered: case={files['case']}, items={files['items']}, "
        f"candidates={files['candidates']}, references={files['references']}, "
        f"raters={files['raters']}, materials={len(files['materials'])} files")

    # 2. Load case.json
    case_data = load_json(files["case"])
    if case_data is None:
        # Create minimal case
        case_data = {"case_id": "UNKNOWN", "task_instruction": "No case.json found"}
        log("WARNING: No case.json found; using minimal default")

    case_id = case_data.get("case_id", "UNKNOWN")
    output_contract = case_data.get("output_contract", {})
    task_instruction = case_data.get("task_instruction", "")

    # 3. Load structured inputs
    items_data = load_json(files["items"])
    candidates_data = load_json(files["candidates"])
    references_data = load_json(files["references"])
    raters_data = load_json(files["raters"])

    # 4. Read materials if available
    materials_text = ""
    for mat_path in files["materials"]:
        text = read_text_file(mat_path)
        if text:
            materials_text += f"\n\n--- {os.path.basename(mat_path)} ---\n\n{text}"

    # 5. Derive items if none provided
    if not items_data:
        if materials_text.strip():
            log("No items file found; deriving items from materials")
            items = derive_items(materials_text)
        else:
            log("No items and no materials; cannot derive items")
            items = []
    else:
        items = items_data if isinstance(items_data, list) else items_data.get("items", [])

    # Build lookup maps
    cand_map = {}
    if candidates_data:
        cand_list = candidates_data if isinstance(candidates_data, list) else candidates_data.get("items", candidates_data.get("answers", []))
        for c in cand_list:
            if isinstance(c, dict) and "item_id" in c:
                cand_map[c["item_id"]] = c

    ref_map = {}
    if references_data:
        ref_list = references_data if isinstance(references_data, list) else references_data.get("items", references_data.get("references", []))
        for r in ref_list:
            if isinstance(r, dict) and "item_id" in r:
                ref_map[r["item_id"]] = r

    # 6. Score each item
    scored_items = []
    for item in items:
        iid = item.get("item_id", "unknown")
        candidate = cand_map.get(iid)
        reference = ref_map.get(iid)
        result = score_item(item, candidate, reference)
        scored_items.append(result)

    # 7. Compute aggregates
    total_score = sum(r["score"] for r in scored_items)
    max_total = sum(r["max_score"] for r in scored_items)
    n_items = len(scored_items)
    mean_score = total_score / n_items if n_items else 0
    pass_count = sum(1 for r in scored_items if r["verdict"] == "pass")
    pass_rate = pass_count / n_items if n_items else 0

    aggregates = {
        "n_items": n_items,
        "total_score": total_score,
        "max_total": max_total,
        "mean_score": round(mean_score, 2),
        "pass_rate": round(pass_rate, 3),
    }

    # 8. Failure taxonomy
    taxonomy = build_failure_taxonomy(scored_items)

    # 9. Rater agreement
    agreement = compute_agreement(raters_data)

    # 10. Improvement suggestions
    suggestions = suggest_improvements(scored_items, agreement)

    # 11. Preserved facts
    preserved_facts = extract_preserved_facts(scored_items, materials_text)

    # 12. Evidence map
    evidence_map = {}
    for r in scored_items:
        evidence_map[r["item_id"]] = r.get("evidence", {})

    # 13. Uncertainties
    uncertainties = []
    if not files["references"]:
        uncertainties.append("No reference answers provided; scores reflect structural quality only, not factual correctness.")
    if not files["raters"]:
        uncertainties.append("No rater data provided; inter-rater agreement not computed.")
    if not items_data and not materials_text:
        uncertainties.append("No items or materials found; benchmark is empty.")
    if not files["candidates"]:
        uncertainties.append("No candidate answers provided; all items scored as missing_output.")

    # 14. Change log
    change_log = [
        {"action": "pipeline_run",
         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "details": f"Evaluated {n_items} items; items_derived={not bool(items_data)}; "
                    f"has_candidates={bool(candidates_data)}; has_references={bool(references_data)}; "
                    f"has_raters={bool(raters_data)}"},
    ]

    # 15. Determine status
    if n_items == 0:
        status = "input_error"
    elif not files["candidates"] and not candidates_data:
        status = "reference_only"  # benchmark items delivered but no candidates to evaluate
    elif pass_rate > 0:
        status = "answered"
    else:
        status = "answered"  # Even if all fail, the evaluation itself is complete

    # 16. Run record
    input_hashes = {}
    for key, path in files.items():
        if isinstance(path, str) and os.path.isfile(path):
            input_hashes[os.path.basename(path)] = sha256_file(path)
        elif isinstance(path, list):
            for p in path:
                if os.path.isfile(p):
                    input_hashes[os.path.basename(p)] = sha256_file(p)

    run_record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_id": case_id,
        "script": "run.py",
        "python_version": sys.version,
        "input_hashes": input_hashes,
        "elapsed_seconds": round(time.time() - T0, 1),
        "toolchain": _TOOLS_PY if os.path.exists(_TOOLS_PY) else sys.executable,
    }

    # 17. Build result.json
    result = {
        "case_id": case_id,
        "deliverable": {
            "benchmark_items": scored_items,
            "aggregates": aggregates,
            "failure_taxonomy": taxonomy,
            "rater_agreement": agreement,
            "improvement_suggestions": suggestions,
        },
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "status": status,
        "sources": [os.path.basename(p) for p in (files["materials"] or [])],
        "uncertainties": uncertainties,
        "limitations": uncertainties,
        "run_record": run_record,
    }

    # 18. Write outputs
    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"Wrote {result_path}")

    # Per-item TSV
    tsv_path = os.path.join(output_dir, "per_item.tsv")
    write_per_item_tsv(scored_items, tsv_path)
    log(f"Wrote {tsv_path}")

    # Report
    report_text = generate_report(case_id, scored_items, aggregates, taxonomy,
                                  agreement, suggestions, uncertainties)
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    log(f"Wrote {report_path}")

    # Selfcheck
    selfcheck = {
        "case_id_matches": case_id == case_data.get("case_id"),
        "all_contract_fields_present": all(
            k in result for k in ["case_id", "deliverable", "preserved_facts",
                                   "evidence_map", "change_log"]),
        "items_scored": n_items,
        "artifacts_exist": {
            "result.json": os.path.isfile(result_path),
            "per_item.tsv": os.path.isfile(tsv_path),
            "report.md": os.path.isfile(report_path),
        },
        "status": status,
    }
    sc_path = os.path.join(output_dir, "selfcheck.json")
    with open(sc_path, "w", encoding="utf-8") as f:
        json.dump(selfcheck, f, ensure_ascii=False, indent=2)
    log(f"Wrote {sc_path}")

    log(f"Pipeline complete: {n_items} items, status={status}")
    return 0

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Academic Writing Benchmark & Evaluation Tool — single entry point")
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                    help="Input directory (default: /input)")
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                    help="Output directory (default: /app/output)")
    args = ap.parse_args()

    log(f"python={sys.executable}")
    log(f"input={args.input}")
    log(f"output={args.output}")

    try:
        return run_pipeline(args.input, args.output)
    except Exception as e:
        log(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        # Write a minimal result.json even on error
        os.makedirs(args.output, exist_ok=True)
        err_result = {
            "case_id": "ERROR",
            "deliverable": {"benchmark_items": [], "aggregates": {},
                           "failure_taxonomy": {}, "rater_agreement": {},
                           "improvement_suggestions": []},
            "preserved_facts": [],
            "evidence_map": {},
            "change_log": [{"action": "error", "details": str(e)}],
            "status": "input_error",
            "sources": [],
            "uncertainties": [f"Pipeline error: {e}"],
            "limitations": [f"Pipeline error: {e}"],
            "run_record": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "error": str(e)},
        }
        with open(os.path.join(args.output, "result.json"), "w") as f:
            json.dump(err_result, f, ensure_ascii=False, indent=2)
        return 2


if __name__ == "__main__":
    sys.exit(main())
