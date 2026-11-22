#!/usr/bin/env python3
"""Academic Writing Benchmark & Evaluation Tool.

Produces a writing-benchmark evaluation from input materials:
  - rubric-based per-item scoring with observable anchors
  - acceptable-variant vs. substantive-error separation
  - rater agreement (percent + Cohen's kappa)
  - failure taxonomy and improvement suggestions
  - reproducible run record with input hashes

Inputs (discovered by filename family, never hard-coded):
  case      : case.json                         {case_id, task_instruction?, output_contract?}
  items     : *items*.json / *tasks*.json        [{item_id, prompt, criteria:[{name,weight}], required_sections?}]
  candidates: *answers*.json / *submissions*.json / *drafts*.json  [{item_id, answer}]
  references: *reference*.json / *gold*.json     [{item_id, accept:[...], reject:[...]}]
  raters    : *rater*.json / *annot*.json        [{rater, item_id, scores:{criterion: 0..1}}]
  materials : *.pdf / *.txt / *.md               (used to derive items when no explicit item set)

Output fields (strict output_contract):
  case_id, deliverable, preserved_facts, evidence_map, change_log

Plus auxiliary fields: status, sources, uncertainties, limitations, run_record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Interpreter shim: re-exec under science-python if fitz is needed and missing
# ---------------------------------------------------------------------------
def _ensure_science_python():
    try:
        import fitz  # noqa: F401
        return
    except Exception:
        pass
    for cand in (os.environ.get("WEEKLY_TOOLS_PYTHON"),
                 "/opt/weekly-tools/bin/python",
                 "/opt/weekly-tools/bin/python3"):
        if cand and os.path.exists(cand) and os.path.realpath(cand) != os.path.realpath(sys.executable):
            try:
                os.execv(cand, [cand] + sys.argv)
            except Exception:
                continue


_ensure_science_python()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SKIP_NAMES = {"result.json", "per_item.tsv", "report.md", "selfcheck.json",
              "run.md", "skill.md", "gain.md", "rubric.md"}

FAMILIES = {
    "items":      ("items", "tasks", "questions"),
    "candidates": ("answers", "submissions", "drafts"),
    "references": ("reference", "gold", "expected"),
    "raters":     ("rater", "annot", "judge"),
}


# ---------------------------------------------------------------------------
# PDF text extraction (pure stdlib fallback + optional fitz / pdftotext)
# ---------------------------------------------------------------------------
def _pure_pdf_text(path: str | Path) -> list[str]:
    """Extract text from PDF using only the standard library (zlib + regex)."""
    import zlib as _zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    TOKEN = re.compile(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?")
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        raw = m.group(1)
        try:
            raw = _zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        parts: list[str] = []
        in_array = False
        for t in TOKEN.finditer(text):
            tok = t.group(0)
            if tok == "[":
                in_array = True
                continue
            if tok == "]":
                in_array = False
                continue
            if tok.startswith("("):
                parts.append(tok[1:-1])
                continue
            if in_array:
                try:
                    if float(tok) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        if parts:
            joined = "".join(parts)
            joined = (joined
                      .replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                      .replace("\\n", " ").replace("\\r", " ").replace("\\t", " "))
            chunks.append(joined)
    if not chunks:
        return []
    text = "\n\n".join(chunks)
    return text.split("\f") or [text]


def read_text_any(path: Path) -> list[str]:
    """Read text from .txt/.md/.json or extract from .pdf (multiple fallbacks)."""
    if path.suffix.lower() != ".pdf":
        try:
            return [path.read_text(encoding="utf-8", errors="replace")]
        except Exception:
            return []
    # Try pure-stdlib PDF extraction first
    pages = _pure_pdf_text(path)
    if pages:
        return pages
    # Try PyMuPDF
    try:
        import fitz
        return [pg.get_text("text") for pg in fitz.open(str(path))]
    except Exception:
        pass
    # Try pdftotext CLI
    try:
        r = subprocess.run(["which", "pdftotext"], capture_output=True)
        if r.returncode == 0:
            r2 = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                capture_output=True, text=True, timeout=120)
            if r2.returncode == 0 and r2.stdout.strip():
                return r2.stdout.split("\f")
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
NUM_UNIT = re.compile(
    r"(?<![A-Za-z0-9_\-])[-+]?\d+(?:\.\d+)?"
    r"(?:\s*[±+\-/]{1,4}\s*\d+(?:\.\d+)?)?\s*"
    r"(?:mm3|%|nM|µM|mg/kg|mg|ml|µg|ng|mol|days?|weeks?|months?|years?|"
    r"patients?|samples?|subjects?|cells?|genes?|proteins?|"
    r"°C|°F|Hz|kHz|MHz|GHz|nm|µm|mm|cm|m|km|kg|g|mg|µg|ng)")
FACT = re.compile(r"\b(was|were|is|are|showed|reported|observed|measured|"
                  r"found|demonstrated|revealed|indicated|confirmed)\b", re.I)
HEAD = re.compile(r"^\s*(#{1,3}\s*)?([A-Z][A-Za-z /&\-]{2,40})\s*$")


def sha(p: Path) -> str:
    """SHA-256 first 16 hex chars of a file."""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def norm(t) -> str:
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", str(t or "")).strip()


def load_json(p: Path):
    """Load JSON, return None on failure."""
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def as_list(doc) -> list[dict]:
    """Coerce a JSON doc into a list of dicts."""
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        for key in ("items", "tasks", "answers", "records",
                     "annotations", "data", "submissions", "references",
                     "raters", "judges", "results"):
            if isinstance(doc.get(key), list):
                return [x for x in doc[key] if isinstance(x, dict)]
        return [doc]
    return []


def discover(root: Path) -> tuple[dict, dict[str, str]]:
    """Discover input files by filename family; return (found, hashes)."""
    found: dict[str, list] = {k: [] for k in FAMILIES}
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        low = path.name.lower()
        if low in SKIP_NAMES or low.startswith("case.json"):
            continue
        fam = next((f for f, toks in FAMILIES.items()
                    if any(t in low for t in toks)), None)
        if not fam:
            continue
        doc = load_json(path)
        rows = as_list(doc) if doc is not None else []
        if rows:
            found[fam].extend({**r, "_file": path.name} for r in rows)
            hashes[path.name] = sha(path)
    return found, hashes


def derive_items(root: Path, limit: int = 8) -> tuple[list[dict], list[dict],
                                                        dict[str, str], list[dict]]:
    """Build writing-rubric items from materials when the case ships none.

    Returns (items, refs, hashes, preserved_facts_list).
    Each material section with factual sentences becomes one item whose
    criteria are accuracy/completeness/structure; the reference answer
    is the section's own factual sentences.
    """
    items, refs, hashes = [], [], {}
    preserved_facts: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        low = path.name.lower()
        if low.startswith("case.json") or low in SKIP_NAMES:
            continue
        pages = read_text_any(path)
        hashes[path.name] = sha(path)
        section = ""
        for pno, text in enumerate(pages, 1):
            in_refs = False
            for line in (text or "").splitlines():
                if re.match(r"^\s*(references|bibliography|参考文献)\s*$", line, re.I):
                    in_refs = True
                if in_refs:
                    continue
                m = HEAD.match(line)
                if m and len(m.group(2).split()) <= 5:
                    section = m.group(2).strip()
                    continue
                sent = norm(line)
                if len(sent) < 30:
                    continue
                nums = NUM_UNIT.findall(sent)
                if not nums and not FACT.search(sent):
                    continue
                iid = f"W{len(items) + 1}"
                items.append({
                    "item_id": iid,
                    "prompt": (f"Rewrite the '{section or 'results'}' content "
                               f"preserving every stated fact and value"),
                    "required_sections": [section] if section else [],
                    "criteria": [{"name": "accuracy", "weight": 2},
                                 {"name": "completeness", "weight": 1},
                                 {"name": "structure", "weight": 1}],
                })
                refs.append({
                    "item_id": iid,
                    "accept": [sent[:300]],
                    "reject": [],
                    "quote": sent[:300],
                    "locator": f"{path.name}:p{pno}",
                })
                # Record preserved facts
                for n in nums:
                    preserved_facts.append({
                        "value": n,
                        "context": sent[:120],
                        "source": f"{path.name}:p{pno}",
                    })
                if len(items) >= limit:
                    return items, refs, hashes, preserved_facts
    return items, refs, hashes, preserved_facts


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------
def sections_present(answer: str, wanted: list[str]) -> list[str]:
    low = answer.lower()
    return [w for w in wanted if w.lower() not in low]


def cohen_kappa(pairs: list[tuple[int, int]]) -> float | None:
    """Cohen's kappa for two raters on categorical scores."""
    if len(pairs) < 2:
        return None
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    if len(cats) < 2:
        return 1.0 if len({a for a, _ in pairs}) == 1 else 0.0
    total = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / total
    pa = {c: sum(1 for a, _ in pairs if a == c) / total for c in cats}
    pb = {c: sum(1 for _, b in pairs if b == c) / total for c in cats}
    expected = sum(pa[c] * pb[c] for c in cats)
    if expected >= 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Academic Writing Benchmark evaluation tool")
    ap.add_argument("--input", default="/input",
                    help="Input directory (default: /input)")
    ap.add_argument("--output", default="/app/output",
                    help="Output directory (default: /app/output)")
    args = ap.parse_args()

    inp = Path(args.input).resolve()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Read case_id from case.json
    # ------------------------------------------------------------------
    case_id = "unreported"
    case_doc: dict = {}
    case_path = inp / "case.json"
    if case_path.is_file():
        case_doc = load_json(case_path) or {}
        case_id = str(case_doc.get("case_id") or "unreported")

    # ------------------------------------------------------------------
    # 2. Discover structured inputs (items, candidates, refs, raters)
    # ------------------------------------------------------------------
    found, file_hashes = discover(inp)

    items = {str(i.get("item_id") or i.get("id")): i
             for i in found["items"]}

    # ------------------------------------------------------------------
    # 3. If no items found, derive from materials
    # ------------------------------------------------------------------
    derived_preserved_facts: list[dict] = []
    if not items:
        d_items, d_refs, d_hashes, derived_preserved_facts = derive_items(inp)
        if d_items:
            items = {i["item_id"]: i for i in d_items}
            found["references"] = d_refs
            file_hashes.update(d_hashes)

    cands = {str(c.get("item_id") or c.get("id")): c
             for c in found["candidates"]}
    refs = {str(r.get("item_id") or r.get("id")): r
            for r in found["references"]}

    raters: dict[str, dict[str, dict]] = {}
    for r in found["raters"]:
        who = str(r.get("rater") or r.get("annotator") or r.get("_file"))
        raters.setdefault(who, {})[
            str(r.get("item_id") or r.get("id"))] = r.get("scores") or {}

    # ------------------------------------------------------------------
    # 4. Track uncertainties & limitations
    # ------------------------------------------------------------------
    uncertainties: list[str] = []
    limitations = [
        "Scoring uses rubric-weighted summation; anchors are string/structural "
        "matching — semantic-level judgement still requires human or LLM review.",
        "Cohen's kappa is computed only for two raters on the same items; "
        "degenerates on small samples or single-category distributions.",
        "PDF text extraction uses stdlib fallback; complex layouts may lose "
        "structure or merge adjacent columns.",
    ]
    if not items:
        uncertainties.append(
            "No item set found (*items*.json / *tasks*.json) and no derivable "
            "materials — deliverable is empty.")
    if items and not cands:
        uncertainties.append(
            "No candidates (*answers*.json / *submissions*.json / *drafts*.json) "
            "— delivering benchmark items + reference answers + anchors only.")
    if not refs and items:
        uncertainties.append(
            "No reference answers — scoring is structural only; "
            "accuracy scores are not meaningful without references.")
    if not raters:
        uncertainties.append(
            "No rater records (*rater*.json / *annot*.json) — "
            "inter-rater agreement cannot be computed.")

    # ------------------------------------------------------------------
    # 5. Score each item
    # ------------------------------------------------------------------
    rows: list[dict] = []
    evidence_map: dict[str, list[dict]] = {}
    preserved_facts: list[dict] = list(derived_preserved_facts)

    for iid, item in sorted(items.items()):
        crit = item.get("criteria") or [{"name": "overall", "weight": 1.0}]
        wsum = sum(float(c.get("weight") or 1.0) for c in crit) or 1.0
        cand = cands.get(iid)
        answer = norm((cand or {}).get("answer") or (cand or {}).get("draft") or "")
        ref = refs.get(iid) or {}

        row: dict = {
            "item_id": iid,
            "prompt": item.get("prompt", ""),
            "max_score": round(wsum, 3),
            "score": 0.0,
            "criteria": [],
            "failure": None,
            "answer_chars": len(answer),
        }

        # Evidence: map item to its source materials
        item_evidence: list[dict] = []
        if ref:
            item_evidence.append({
                "type": "reference",
                "locator": ref.get("locator", ref.get("_file", "unknown")),
                "quote": (ref.get("accept") or [""])[0][:200] if ref.get("accept") else "",
            })
        if cand and cand.get("_file"):
            item_evidence.append({
                "type": "candidate",
                "locator": cand.get("_file", "unknown"),
            })
        evidence_map[iid] = item_evidence

        # Record preserved facts from reference
        for acc in (ref.get("accept") or []):
            for n in NUM_UNIT.findall(acc):
                preserved_facts.append({
                    "value": n,
                    "context": acc[:120],
                    "source": ref.get("locator", ref.get("_file", "unknown")),
                    "item_id": iid,
                })

        # No answer?
        if not answer:
            if cand is None and ref:
                _acc = ref.get("accept") or []
                row.update(
                    verdict="reference_only",
                    reference_answer=norm(_acc[0] if _acc else "")[:300],
                    reference_locator=ref.get("locator"),
                    notes="Benchmark item + reference answer generated; "
                          "no candidate submitted for scoring.",
                )
                rows.append(row)
                continue
            row["failure"] = "missing_output" if cand is None else "empty_output"
            row["verdict"] = "empty"
            rows.append(row)
            continue

        # Structural check: required sections
        missing = sections_present(answer, item.get("required_sections") or [])

        # Variant check: accept/reject
        accept = [norm(a) for a in (ref.get("accept") or [])]
        reject = [norm(a) for a in (ref.get("reject") or [])]
        accepted = any(a.lower() in answer.lower() for a in accept) if accept else None
        rejected = [r for r in reject if r.lower() in answer.lower()]

        # Per-criterion scoring
        earned = 0.0
        for c in crit:
            name = str(c.get("name") or "overall")
            w = float(c.get("weight") or 1.0)
            got = 1.0
            why = "anchor satisfied"
            if missing and name.lower() in ("structure", "completeness", "format"):
                got, why = 0.0, "missing section(s): " + ", ".join(missing)
            nl = name.lower()
            if rejected and any(t in nl for t in ("accuracy", "fact", "citation")):
                got, why = 0.0, "contains rejected variant: " + rejected[0][:80]
            if accepted is False and any(t in nl for t in ("accuracy", "fact", "citation")):
                got, why = 0.5, "not matched by any accepted variant"
            earned += got * w
            row["criteria"].append({
                "criterion": name, "weight": w,
                "score": got, "why": why,
            })

        row["score"] = round(earned, 3)
        if missing:
            row["failure"] = "wrong_format: missing " + ", ".join(missing)
        elif rejected:
            row["failure"] = "wrong_fact: rejected variant present"
        elif accepted is False:
            row["failure"] = "style_or_variant: differs from every accepted variant"
        row["verdict"] = "correct" if not row["failure"] else "partial"
        rows.append(row)

    # ------------------------------------------------------------------
    # 6. Rater agreement
    # ------------------------------------------------------------------
    agreement: dict[str, dict] = {}
    if len(raters) >= 2:
        names = sorted(raters)[:2]
        a_scores, b_scores = raters[names[0]], raters[names[1]]
        common = sorted(set(a_scores) & set(b_scores))
        per_crit: dict[str, list[tuple[int, int]]] = {}
        for iid in common:
            for crit_name, val in (a_scores[iid] or {}).items():
                if crit_name in (b_scores[iid] or {}):
                    per_crit.setdefault(crit_name, []).append(
                        (int(round(float(val))),
                         int(round(float(b_scores[iid][crit_name])))))
        for crit_name, pairs in per_crit.items():
            agree_pct = sum(1 for x, y in pairs if x == y) / len(pairs)
            agreement[crit_name] = {
                "n": len(pairs),
                "percent_agreement": round(agree_pct, 4),
                "cohen_kappa": cohen_kappa(pairs),
            }

    # ------------------------------------------------------------------
    # 7. Aggregate stats
    # ------------------------------------------------------------------
    n = len(rows)
    total_score = sum(r["score"] for r in rows)
    max_total = sum(r["max_score"] for r in rows) or 1.0

    taxonomy: dict[str, list[str]] = {}
    for r in rows:
        if r.get("failure"):
            key = r["failure"].split(":")[0]
            taxonomy.setdefault(key, []).append(r["item_id"])

    pass_count = sum(1 for r in rows if r.get("verdict") == "correct")

    # ------------------------------------------------------------------
    # 8. Build deliverable (the benchmark evaluation product)
    # ------------------------------------------------------------------
    deliverable = {
        "benchmark_items": [{
            "item_id": r["item_id"],
            "prompt": r.get("prompt", ""),
            "verdict": r.get("verdict", "unknown"),
            "score": r["score"],
            "max_score": r["max_score"],
            "failure": r.get("failure"),
            "criteria": r["criteria"],
        } for r in rows],
        "aggregates": {
            "n_items": n,
            "total_score": round(total_score, 3),
            "max_total": round(max_total, 3),
            "mean_score": round(total_score / n, 4) if n else 0.0,
            "pass_rate": round(pass_count / n, 4) if n else 0.0,
        },
        "failure_taxonomy": taxonomy,
        "rater_agreement": agreement,
        "improvement_suggestions": _build_suggestions(rows, agreement),
    }

    # If no candidates, also include the reference benchmark
    if items and not cands:
        deliverable["reference_benchmark"] = [{
            "item_id": i["item_id"],
            "prompt": i.get("prompt", ""),
            "criteria": i.get("criteria", []),
            "required_sections": i.get("required_sections", []),
            "reference_answer": norm(
                (refs.get(i["item_id"]) or {}).get("accept", [""])[0]
            )[:300] if refs.get(i["item_id"]) else None,
            "reference_locator": (refs.get(i["item_id"]) or {}).get("locator"),
        } for i in items.values()]

    # ------------------------------------------------------------------
    # 9. Build change_log
    # ------------------------------------------------------------------
    change_log_entries: list[str] = []
    change_log_entries.append(
        "v1: Rubric-weighted scoring with accept/reject variant matching, "
        "structural anchor checks, rater agreement (percent + Cohen's kappa), "
        "and failure taxonomy.")
    if derived_preserved_facts:
        change_log_entries.append(
            f"Derived {len(items)} benchmark items from source materials "
            f"({len(derived_preserved_facts)} factual values preserved).")
    if not cands and items:
        change_log_entries.append(
            "No candidate answers supplied — delivered reference benchmark "
            "(items + anchors + reference answers). Manual review required "
            "before using scores for high-stakes decisions.")
    for u in uncertainties:
        change_log_entries.append(f"Unresolved: {u}")

    # ------------------------------------------------------------------
    # 10. Build run_record
    # ------------------------------------------------------------------
    run_record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "case_id": case_id,
        "items": n,
        "candidates": len(cands),
        "references": len(refs),
        "raters": sorted(raters),
        "inputs_sha256_16": file_hashes,
        "script": "writing_bench.py",
        "python": sys.version.split()[0],
    }

    # ------------------------------------------------------------------
    # 11. Assemble output_contract payload
    # ------------------------------------------------------------------
    status = "answered" if rows and any(r.get("verdict") != "empty" for r in rows) else "partial"
    if not items:
        status = "insufficient_evidence"

    payload = {
        # === output_contract fields (exact names) ===
        "case_id": case_id,
        "deliverable": deliverable,
        "preserved_facts": preserved_facts if preserved_facts else [
            {"note": "No numeric/factual values extracted from input materials.",
             "source": "case.json"}
        ],
        "evidence_map": evidence_map if evidence_map else {
            "_note": "No benchmark items mapped to sources."
        },
        "change_log": change_log_entries,
        # === auxiliary fields ===
        "status": status,
        "sources": [{"name": k, "sha256_16": v} for k, v in file_hashes.items()],
        "uncertainties": uncertainties,
        "limitations": limitations,
        "run_record": run_record,
    }

    # ------------------------------------------------------------------
    # 12. Write outputs
    # ------------------------------------------------------------------
    (out / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # per_item.tsv
    tsv_lines = ["item_id\tverdict\tscore\tmax_score\tfailure\tchars"]
    for r in rows:
        tsv_lines.append("\t".join(
            str(r.get(k) or "").replace("\t", " ")[:160]
            for k in ("item_id", "verdict", "score", "max_score",
                       "failure", "answer_chars")))
    (out / "per_item.tsv").write_text("\n".join(tsv_lines) + "\n",
                                      encoding="utf-8")

    # report.md
    rep = _build_report(rows, agreement, run_record, uncertainties,
                        deliverable["aggregates"])
    (out / "report.md").write_text(rep, encoding="utf-8")

    # SELFCHECK.json
    check = {
        "items": n,
        "with_candidates": len(cands),
        "raters": len(raters),
        "every_item_has_verdict": all(r.get("verdict") for r in rows),
        "taxonomy_covers_all_failures": (
            sum(len(v) for v in taxonomy.values())
            == sum(1 for r in rows if r.get("failure"))),
        "agreement_computed": bool(agreement),
        "output_contract_fields_present": all(
            k in payload for k in ("case_id", "deliverable",
                                    "preserved_facts", "evidence_map",
                                    "change_log")),
    }
    (out / "SELFCHECK.json").write_text(
        json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps(check, ensure_ascii=False))
    return 0


def _build_suggestions(rows: list[dict],
                       agreement: dict) -> list[str]:
    """Generate improvement suggestions from scoring results."""
    suggestions: list[str] = []
    for r in rows:
        f = r.get("failure") or ""
        if "missing" in f:
            suggestions.append(
                f"{r['item_id']}: Add the missing section(s) to satisfy "
                f"the structure anchor.")
        elif "rejected" in f:
            suggestions.append(
                f"{r['item_id']}: Rewrite to remove the rejected variant; "
                f"preserve original facts and citations.")
        elif "style_or_variant" in f:
            suggestions.append(
                f"{r['item_id']}: The answer differs from all accepted "
                f"variants — verify factual accuracy or add to accept list.")
    for crit, ag in agreement.items():
        k = ag.get("cohen_kappa")
        if k is not None and k < 0.4:
            suggestions.append(
                f"Anchor '{crit}' has low agreement (κ={k}): "
                f"revise the anchor wording for observability, "
                f"do NOT adjust scores to force agreement.")
    if not suggestions:
        suggestions.append("All items passed; no specific improvements needed.")
    return suggestions


def _build_report(rows: list[dict], agreement: dict,
                  run_record: dict, uncertainties: list[str],
                  aggregates: dict) -> str:
    """Build a human-readable Markdown report."""
    lines = [
        "# Writing Benchmark Evaluation Report",
        "",
        f"- **Case ID**: {run_record['case_id']}",
        f"- **Items**: {aggregates['n_items']}",
        f"- **Total score**: {aggregates['total_score']} / {aggregates['max_total']}",
        f"- **Mean score**: {aggregates['mean_score']}",
        f"- **Pass rate**: {aggregates['pass_rate']}",
        f"- **Timestamp**: {run_record['at']}",
        "",
    ]
    for r in rows:
        lines.append(f"## {r['item_id']} — {r.get('verdict', '?')}"
                      f"（{r['score']}/{r['max_score']}）")
        if r.get("prompt"):
            lines.append(f"**Prompt**: {r['prompt'][:200]}")
        if r.get("failure"):
            lines.append(f"- **Failure**: {r['failure']}")
        for c in r.get("criteria", []):
            lines.append(
                f"- {c['criterion']}: {c['score']}×w{c['weight']} — {c['why']}")
        if r.get("reference_answer"):
            # 题面要求"事实与来源映射：可逐条回溯"——每条参考答复都要带材料定位
            lines.append(f"- **参考答复（材料原文）**: {str(r['reference_answer'])[:300]}")
            lines.append(f"- **定位**: `{r.get('reference_locator') or 'not_reported'}`")
        lines.append("")

    mapped = [r for r in rows if r.get("reference_answer")]
    if mapped:
        lines += ["## 事实与来源映射", "",
                  "| item | 参考答复（节选） | 定位 |", "|---|---|---|"]
        for r in mapped:
            answer = str(r["reference_answer"]).replace("|", "\|")[:120]
            lines.append(f"| {r['item_id']} | {answer} | `{r.get('reference_locator') or 'not_reported'}` |")
        lines.append("")

    if agreement:
        lines += ["## Rater Agreement", ""]
        for crit, ag in agreement.items():
            lines.append(
                f"- {crit}: n={ag['n']}, "
                f"agreement={ag['percent_agreement']}, "
                f"κ={ag['cohen_kappa']}")
        lines.append("")

    if uncertainties:
        lines += ["## Uncertainties", ""]
        for u in uncertainties:
            lines.append(f"- {u}")
        lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
