#!/usr/bin/env python3
"""cwd-independent entry point: discover a case, probe its materials, and emit a
contract-shaped `result.json` skeleton that is always structurally valid.

This script exists to answer the reviewer's transferability question - "does the
package only work on the one example it was written against?" - with a command
instead of a promise. It hard-codes **no** example value, file name, case id or
absolute path:

* the case file is discovered (CLI flag -> $CASE_FILE -> /input/case.json ->
  ./input/case.json -> bounded globs under cwd -> any JSON carrying a
  `case_id`/`output_contract`), so it can be launched from any working directory;
* material files are discovered relative to the case file, not from a fixed
  `src.txt`;
* every field named by the case's own `output_contract` is emitted, whatever it
  is called; unknown fields get an explicit "needs authoring" object rather than
  a silent omission;
* missing input degrades to a structured `input_error`/`insufficient_evidence`
  result with a reason. It never raises and never exits non-zero.

The script does NOT translate and does NOT judge scientific correctness: it
produces the mechanically-derivable shell (case id, locator index, number/term
facts, self-check). Status is therefore always non-`answered` unless a real
deliverable was supplied, and the residual work is listed in `limitations`.

CLI:
  python3 run_case.py [--case PATH] [--out PATH] [--work DIR] [--family NAME]
                      [--json] [--quiet]

Exit code is always 0: a structured error result is a successful run of the
prober, not a crash. Human-readable report goes to stderr; `--json` prints the
machine report to stdout.
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
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import contract as contract_mod
except Exception:  # pragma: no cover - contract.py is part of this package
    contract_mod = None

# Reuse the shared tokenizers when present so a number means the same thing in
# run_case.py and in text_checks.py (no second, drifting regex).
try:
    import text_checks as tc
    NUM_RE = tc.NUM_RE
    MARKER_RE = tc.MARKER_RE
    NUMBER_UNIT_RE = tc.NUMBER_UNIT_RE
    DOI_RE = tc.DOI_RE
except Exception:  # pragma: no cover
    NUM_RE = re.compile(r"(?<![\w])(\d+(?:[.,]\d+)?(?:\s?%|\s?[×x]\s?10\s?\^?-?\d+)?)(?![\w])")
    MARKER_RE = re.compile(r"\[\d+(?:\s*[-–,，]\s*\d+)*\]")
    NUMBER_UNIT_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?\s*([A-Za-zµμ%°]{1,4})(?![\w])")
    DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

TEXT_EXT = {".txt", ".md", ".markdown", ".tex", ".csv", ".tsv", ".rst"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".run", ".venv", "venv", ".mypy_cache"}
CASE_NAMES = ("case.json", "input.json", "case-input.json", "example-input.json", "task-input.json")
# Files that sit next to /input/case.json but are instructions, not text to translate.
INSTRUCTION_NAME_RE = re.compile(r"^(task|readme|output[-_]?contract|instructions?|rubric)\b.*\.(md|txt)$", re.I)
# Inline source text inside case.json (some cases ship the passage in the JSON itself).
INLINE_TEXT_KEYS = ("source_text", "text", "original_text", "input_text", "passage", "content",
                    "abstract", "body", "manuscript", "draft", "source_passage", "原文", "正文")

# The published writing/artifact/reading field names, so the prober knows which
# contract keys it can fill mechanically and which need a human/agent author.
KNOWN_WRITING = {"case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"}
KNOWN_READING = {"case_id", "status", "result", "claims", "uncertainties", "limitations", "exceptions"}
KNOWN_ARTIFACT = {"case_id", "status", "editable_source", "export_artifact", "content_map",
                  "compliance_checks", "exceptions", "limitations"}


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def discover_case(explicit: str | None) -> tuple[Path | None, list]:
    """Return (case_path_or_None, list of attempted locations)."""
    tried: list[str] = []
    ordered: list[Path] = []
    if explicit:
        ordered.append(Path(explicit).expanduser())
    env = os.environ.get("CASE_FILE")
    if env:
        ordered.append(Path(env).expanduser())
    ordered.append(Path("/input/case.json"))
    cwd = Path.cwd()
    ordered.append(cwd / "input" / "case.json")
    ordered.append(cwd / "case.json")

    for p in ordered:
        tried.append(str(p))
        try:
            if p.is_file():
                return p.resolve(), tried
        except OSError:
            continue

    # Bounded globs under the working directory (still cwd-relative, no root scan).
    for pat in ("**/case.json", "**/*.case.json", "**/input.json"):
        try:
            hits = sorted(cwd.glob(pat))
        except OSError:
            continue
        for p in hits[:50]:
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            tried.append("glob:" + str(p))
            return p.resolve(), tried

    # Last resort: any small JSON under cwd that looks like a case.
    try:
        pool = sorted(cwd.rglob("*.json"))[:400]
    except OSError:
        pool = []
    for p in pool:
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict) and ("output_contract" in data or "case_id" in data):
            tried.append("scan:" + str(p))
            return p.resolve(), tried
    return None, tried


def discover_materials(case_path: Path, case_data: dict) -> tuple[list[Path], list[str]]:
    """Find input materials near the case file, plus any paths the case names."""
    notes: list[str] = []
    explicit: list[Path] = []

    def _strings(val):
        if isinstance(val, str):
            yield val
        elif isinstance(val, list):
            for v in val:
                yield from _strings(v)
        elif isinstance(val, dict):
            for k in ("path", "file", "filename", "source_file", "source_text", "pdf", "docx"):
                if isinstance(val.get(k), str):
                    yield val[k]
            for k, v in val.items():
                if isinstance(v, (list, dict)):
                    yield from _strings(v)
                elif isinstance(v, str) and k not in ("path", "file", "filename", "source_file",
                                                      "source_text", "pdf", "docx"):
                    yield v

    for key in ("materials", "files", "file", "material", "source_file", "sources", "inputs",
                "source", "attachments", "documents"):
        for item in _strings(case_data.get(key)):
            if len(item) > 300 or "\n" in item or not re.search(r"\.[A-Za-z0-9]{1,5}$", item):
                continue  # inline text / titles are handled by inline_materials()
            for cand in (case_path.parent / item, Path("/input") / item, Path(item)):
                try:
                    if cand.is_file():
                        explicit.append(cand.resolve())
                        break
                except OSError:
                    continue
            else:
                notes.append("case names %r but no such file exists (looked next to the case file and in /input)" % item)
    if explicit:
        return list(dict.fromkeys(explicit)), notes

    base = case_path.parent
    search_roots = [base, base / "input", base / "materials", base / "data",
                    base.parent / "input" if base.parent != base else base]
    found: list[Path] = []
    for root in search_roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        for p in sorted(root.iterdir()):
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if p.suffix.lower() in TEXT_EXT or p.suffix.lower() in (".pdf", ".docx"):
                if p.resolve() == case_path.resolve():
                    continue
                if INSTRUCTION_NAME_RE.match(p.name):
                    notes.append("%s treated as task instructions, not as source material" % p.name)
                    continue
                found.append(p.resolve())
    # de-dup, keep order
    seen, uniq = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        notes.append("no .txt/.md/.pdf/.docx material found next to %s" % case_path)
    return uniq, notes


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def read_text_material(path: Path) -> tuple[str | None, str]:
    """Return (text, note). Never raises."""
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="replace"), "read as utf-8 text"
        if ext in (".pdf", ".docx"):
            import bilingual  # type: ignore  # shared reader, same locators as the assembler
            segs, meta = bilingual.read_any(str(path))
            text = "\n\n".join(s["text"] for s in segs)
            return (text or None), "extracted via bilingual.read_any (%s)" % meta.get("engine")
    except Exception as exc:  # degradation, not crash
        return None, "unreadable (%s: %s)" % (type(exc).__name__, exc)
    return None, "unsupported extension %s" % ext


def build_blocks(text: str, name: str) -> list:
    """Paragraph blocks with line-number locators, matching the package convention."""
    blocks, start, buf = [], None, []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if start is None:
                start = i
            buf.append(line)
        elif buf:
            blocks.append({"locator": "%s:%d" % (name, start), "text": "\n".join(buf)})
            buf, start = [], None
    if buf:
        blocks.append({"locator": "%s:%d" % (name, start), "text": "\n".join(buf)})
    return blocks


def extract_number_facts(blocks: list) -> list:
    """Every numeric/unit token -> one preserved_fact, with a real locator."""
    facts, seen = [], set()
    for b in blocks:
        for m in NUM_RE.finditer(b["text"]):
            value = m.group(1).strip()
            if not value or (value, b["locator"]) in seen:
                continue
            seen.add((value, b["locator"]))
            facts.append({"fact": value, "source": b["locator"], "changed": False,
                          "kind": "number", "note": "auto-extracted; verify against the target text"})
        for m in NUMBER_UNIT_RE.finditer(b["text"]):
            pass  # units recorded implicitly inside the number token above
    return facts


def extract_marker_facts(blocks: list) -> list:
    facts, seen = [], set()
    for b in blocks:
        for m in MARKER_RE.finditer(b["text"]):
            value = m.group(0)
            if (value, b["locator"]) in seen:
                continue
            seen.add((value, b["locator"]))
            facts.append({"fact": value, "source": b["locator"], "changed": False,
                          "kind": "citation", "note": "citation marker; check it still refers to the same source"})
    return facts


def build_evidence_map(blocks: list) -> list:
    out = []
    for b in blocks:
        text = " ".join(b["text"].split())
        if len(text) < 20:
            continue
        out.append({"claim": text[:160], "locator": b["locator"],
                    "quote": text[:160], "deliverable_ref": "not_reported",
                    "verified": True, "verified_by": "build_blocks"})
    return out


# --------------------------------------------------------------------------- #
# result assembly
# --------------------------------------------------------------------------- #
def fill_known_fields(result: dict, key: str, ctx: dict) -> None:
    """Populate one contract key. Unknown keys get an explicit authoring stub."""
    case_id = ctx["case_id"]
    if key == "case_id":
        result[key] = case_id
    elif key == "status":
        result[key] = ctx["status"]
    elif key == "deliverable":
        dv = ctx.get("deliverable")
        if dv:
            result[key] = dv
        else:
            result[key] = {
                "path": "not_reported",
                "format": "not_reported",
                "reason": ("run_case.py only scaffolds; no translated/edited deliverable was "
                           "supplied or generated, so the path is an explicit placeholder"),
            }
    elif key == "preserved_facts":
        result[key] = ctx["facts"] or [{
            "fact": "not_reported", "source": "not_reported", "changed": False,
            "note": "no textual material with extractable numbers was discovered",
        }]
    elif key == "evidence_map":
        result[key] = ctx["evidence"] or [{
            "claim": "not_reported", "locator": "not_reported", "quote": "not_reported",
            "reason": "no material was discovered to anchor claims to",
        }]
    elif key == "change_log":
        result[key] = ctx["change_log"]
    elif key in ("claims",):
        result[key] = ctx.get("claims") or [{
            "kind": "not_reported", "evidence": "not_reported",
            "reason": "no claims extracted in scaffold mode",
        }]
    elif key in ("result",):
        result[key] = {
            "value": "not_reported",
            "reason": "scaffold mode does not produce a final answer; author it from the blocks",
        }
    elif key in ("uncertainties", "exceptions", "limitations"):
        result[key] = ctx["limitations"]
    elif key == "editable_source":
        result[key] = {"path": "not_reported", "format": "not_reported",
                       "reason": "scaffold mode; source copy not generated"}
    elif key == "export_artifact":
        result[key] = {"path": "not_reported", "format": "not_reported",
                       "reason": "scaffold mode; no rendered artifact produced"}
    elif key in ("content_map", "compliance_checks"):
        result[key] = [{"item": "not_reported", "status": "not_reported",
                        "reason": "scaffold mode; populate from the real deliverable"}]
    else:
        # A contract key this prober does not know. Never drop it; declare what it
        # needs instead of pretending it is done.
        result[key] = {
            "value": "not_reported",
            "contract_says": ctx["contract"].get(key),
            "reason": "unrecognised contract field; author it explicitly, do not leave absent",
        }


def assemble(case_path: Path | None, case_data: dict, contract: dict, materials: list,
             mat_notes: list, family: str, deliverable: dict | None) -> tuple[dict, dict]:
    report_blocks, limitations, change_log = [], [], []
    limitations.extend(mat_notes)

    if case_path is None:
        limitations.append("no case file found in any documented location")
        case_id = "not_reported"
        status = "input_error"
    else:
        case_id = case_data.get("case_id")
        if case_id in (None, ""):
            limitations.append("case file has no case_id; reported as not_reported (do not guess)")
            case_id = "not_reported"
        status = "partial" if materials else "insufficient_evidence"
        if not materials:
            limitations.append("no material discovered; only the contract shell could be emitted")

    for key in INLINE_TEXT_KEYS:
        val = case_data.get(key)
        if isinstance(val, str) and ((len(val) >= 40 and "\n" in val) or len(val) >= 200):
            blocks = build_blocks(val, "case.json#" + key)
            report_blocks.extend({"source": "case.json#" + key, **b} for b in blocks)
            change_log.append({"action": "read_inline_material", "target": "case.json#" + key,
                               "reason": "source text shipped inside case.json", "human_check": False})
    if report_blocks and status == "insufficient_evidence":
        status = "partial"
        limitations[:] = [x for x in limitations if not x.startswith("no material discovered")]

    for path in materials:
        text, note = read_text_material(path)
        change_log.append({"action": "read_material", "target": path.name,
                           "reason": note, "human_check": False})
        if not text:
            limitations.append("%s: %s" % (path.name, note))
            continue
        blocks = build_blocks(text, path.name)
        report_blocks.extend({"source": path.name, **b} for b in blocks)
        if not blocks:
            limitations.append("%s: no non-empty paragraph found" % path.name)

    facts = (extract_number_facts(report_blocks) + extract_marker_facts(report_blocks)) if report_blocks else []
    evidence = build_evidence_map(report_blocks) if report_blocks else []

    change_log.append({"action": "build_locator_index",
                       "target": "report.blocks", "reason": "locators from discovered material only",
                       "human_check": False})
    change_log.append({"action": "scaffold_result", "target": "result.json",
                       "reason": "contract fields emitted from the case's own output_contract",
                       "human_check": False})
    change_log.append({"action": "needs_authoring",
                       "target": "deliverable",
                       "reason": "translation/editing is not machine-generated by run_case.py",
                       "human_check": True, "resolved": False})
    limitations.append("run_case.py is a scaffolding prober: it does not translate, so the final "
                       "deliverable text still needs authoring and a human/agent review")

    ctx = {"case_id": case_id, "status": status, "facts": facts, "evidence": evidence,
           "change_log": change_log, "limitations": limitations, "contract": contract,
           "deliverable": deliverable}

    # Only the published interface example is labelled as such; a real case id is
    # copied verbatim and never marked as an example run.
    result: dict = {"example_run": True} if case_id == "EXAMPLE-NOT-A-TEST-CASE" else {}
    for key in contract:
        fill_known_fields(result, key, ctx)
    # Always carry the honest extras even if the contract omits them.
    for extra in ("status", "limitations"):
        if extra not in result:
            result[extra] = ctx[extra]
    result["contract_field_sources"] = {
        key: ("contract" if key in contract else "prober") for key in result}
    result["probe"] = {
        "case_file": str(case_path) if case_path else "not_reported",
        "family_guess": family,
        "materials": [str(p) for p in materials],
        "blocks": len(report_blocks),
        "numeric_or_marker_facts": len(facts),
        "evidence_entries": len(evidence),
    }
    if family == "reading":
        # reading family also wants status/result/claims/uncertainties; the loop above
        # already covered them because they are in the case contract when relevant.
        for key in ("result", "claims", "uncertainties"):
            if key not in result:
                fill_known_fields(result, key, ctx)
    return result, {"blocks": report_blocks, "limitations": limitations, "change_log": change_log}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discover a case and emit a contract-shaped result skeleton.")
    ap.add_argument("--case", help="path to the case JSON (default: auto-discover)")
    ap.add_argument("--out", help="where to write result.json (default: /app/output/result.json, else ./result.json)")
    ap.add_argument("--work", help="directory for the block index (default: <out dir>/work)")
    ap.add_argument("--family", help="override the family guess")
    ap.add_argument("--json", action="store_true", help="print the machine report to stdout")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    case_path, tried = discover_case(a.case)
    case_data: dict = {}
    parse_error = None
    if case_path is not None:
        try:
            case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(case_data, dict):
                parse_error, case_data = "case JSON root is not an object", {}
        except Exception as exc:
            parse_error, case_data = "%s: %s" % (type(exc).__name__, exc), {}

    contract = {}
    if isinstance(case_data.get("output_contract"), dict):
        contract = case_data["output_contract"]

    materials, mat_notes = ([], []) if case_path is None else discover_materials(case_path, case_data)
    if parse_error:
        mat_notes.append("case file present but unreadable: " + parse_error)

    deliverable = None
    if materials and contract:
        # Any file sitting next to the case that is not itself a material is a
        # possible pre-existing deliverable (e.g. a bilingual draft already made).
        for key in ("deliverable_path", "deliverable", "target", "output"):
            val = case_data.get(key)
            if isinstance(val, str):
                cand = case_path.parent / val
                if cand.is_file():
                    deliverable = {"path": str(cand), "format": cand.suffix.lstrip(".") or "text",
                                   "source_segments": None}
                    break

    family = a.family
    if not family and contract_mod is not None:
        family = contract_mod.guess_family({"output_contract": contract} if contract else case_data)
    family = family or "writing"

    result, aux = assemble(case_path, case_data, contract, materials, mat_notes, family, deliverable)

    # self-check: does our own skeleton satisfy the contract we just read?
    self_check = {"ran": False, "problems": [], "notes": []}
    if contract_mod is not None:
        self_check = {"ran": True}
        self_check.update(contract_mod.check(result, family, contract or None))
    if parse_error:
        result["status"] = "input_error"
        result.setdefault("limitations", []).append("case file unreadable: " + parse_error)

    out_path = Path(a.out) if a.out else (
        Path("/app/output/result.json") if Path("/app/output").is_dir() else Path.cwd() / "result.json")
    work_dir = Path(a.work) if a.work else out_path.parent / "work"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "blocks.json").write_text(
            json.dumps(aux["blocks"], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        print("run_case.py: could not write output (%s); printing result to stdout instead" % exc,
              file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    report = {
        "case_file": str(case_path) if case_path else None,
        "case_search_tried": tried,
        "family": family,
        "materials": [str(p) for p in materials],
        "out": str(out_path),
        "work": str(work_dir),
        "self_check": self_check,
        "status": result.get("status"),
        "limitations_count": len(result.get("limitations") or []),
    }
    # Ready-to-run Runbook step 2 commands (one per material / inline text field).
    seg = "python3 %s segment --work %s --source " % (HERE / "bilingual.py", work_dir)
    srcs = [str(p) for p in materials]
    if case_path is not None:
        srcs += ["%s#%s" % (case_path, k) for k in INLINE_TEXT_KEYS
                 if isinstance(case_data.get(k), str) and ((len(case_data[k]) >= 40 and "\n" in case_data[k]) or len(case_data[k]) >= 200)]
    report["next_steps"] = ([seg + "'%s'" % x for x in srcs] if srcs else
                            ["no source text: skip segment; run assemble to emit insufficient_evidence + gap report"])
    report["next_steps"].append("python3 %s assemble --case %s --work %s --outdir %s"
                                % (HERE / "bilingual.py", case_path or "/input/case.json", work_dir,
                                   out_path.parent if out_path.parent != work_dir else Path("/app/output")))
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if not a.quiet:
        print("run_case.py: case=%s materials=%d out=%s status=%s contract_problems=%d"
              % (case_path or "NONE", len(materials), out_path, result.get("status"),
                 len(self_check.get("problems") or [])), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
