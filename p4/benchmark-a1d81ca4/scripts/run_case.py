#!/usr/bin/env python3
"""Runtime entry point: /input/case.json in, a contract-valid result.json out.

The rest of this package is machinery an agent drives. This script is the part the
evaluator actually calls, and it is written to two rules:

  1. **Produce a valid result immediately.** Bootstrap reads the case, extracts the
     material into a locator index, and writes a result.json that already satisfies
     the reading contract - every field present, status honest. An agent then
     improves it. Nothing here waits for a "complete" answer, because a run that is
     cut off at 900s must still leave a checkable file behind.

  2. **Never invent a value.** A field is answered only when the case gave a
     mechanical pattern that matched real text. Everything else is `not_reported`
     with the reason, and says so in `uncertainties`. Filling a field from a label,
     a filename, or the model's memory is the failure this package exists to prevent.

The case shape is discovered, not assumed: organizers change it, and a Skill that
hard-codes key names breaks silently on the next revision. `probe` prints what was
found so a mismatch is visible instead of invisible.

CLI:
  python3 run_case.py probe    --case /input/case.json
  python3 run_case.py bootstrap --case /input/case.json --work /app/work \
                                --out /app/output/result.json
  python3 run_case.py attach   --case /input/case.json --work /app/work \
                                --values values.json --out /app/output/result.json
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract as contract_mod  # noqa: E402
import evidence as evidence_mod  # noqa: E402
import pdf_text  # noqa: E402

CASE_KEYS = ("case_id", "id", "caseId", "task_id")
MATERIAL_KEYS = ("material", "materials", "pdf", "pdf_path", "document", "documents",
                 "source_path", "file", "files", "input", "inputs", "paths")
FIELD_KEYS = ("target_fields", "target-fields", "fields", "questions", "items", "workorder")
TEXT_SUFFIXES = (".txt", ".md", ".text", ".csv", ".json")


# --------------------------------------------------------------------------- #
# case discovery
# --------------------------------------------------------------------------- #
def load_case(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def case_id_of(case: dict) -> str | None:
    for key in CASE_KEYS:
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _walk_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def find_materials(case: dict, case_path: str) -> list[str]:
    """Absolute paths of material files named anywhere in the case, in stated order.

    Candidates come from material-ish keys first, then any string that looks like a
    readable file. Relative paths resolve against the case file and the usual
    runtime roots, because organizers write paths relative to whichever of those
    they had in mind.
    """
    roots = [os.path.dirname(os.path.abspath(case_path)), "/input", "/app/work", os.getcwd()]
    found: list[str] = []

    preferred, other = [], []
    for key, value in _walk_strings(case):
        tail = key.rsplit(".", 1)[-1].split("[")[0].lower()
        if not any(s in value.lower() for s in (".pdf", ".txt", ".md", ".docx", ".json", ".csv")):
            continue
        (preferred if any(k in tail for k in MATERIAL_KEYS) else other).append(value)

    for value in preferred + other:
        for root in roots:
            candidate = value if os.path.isabs(value) else os.path.join(root, value)
            candidate = os.path.normpath(candidate)
            if os.path.isfile(candidate) and candidate not in found:
                if candidate == os.path.abspath(case_path):
                    continue
                found.append(candidate)
                break
    return found


def find_field_specs(case: dict) -> list[dict]:
    """Target field specs, from whichever key this revision used.

    Returns [] when the case asks free-form questions instead of naming fields -
    that is a legitimate shape, and the caller must then say so rather than
    pretending a field list was supplied.
    """
    for key in FIELD_KEYS:
        value = case.get(key)
        if isinstance(value, list) and value and all(isinstance(v, (dict, str)) for v in value):
            specs = []
            for i, item in enumerate(value):
                if isinstance(item, str):
                    specs.append({"field_id": f"item_{i + 1}", "question": item})
                    continue
                fid = (item.get("field_id") or item.get("id") or item.get("name")
                       or item.get("key") or f"item_{i + 1}")
                spec = dict(item)
                spec["field_id"] = str(fid)
                specs.append(spec)
            return specs
    return []


# --------------------------------------------------------------------------- #
# material -> blocks
# --------------------------------------------------------------------------- #
def blocks_from_text(path: str) -> dict:
    """Paragraph blocks for a plain-text material, numbered so locators stay stable."""
    text = open(path, encoding="utf-8", errors="replace").read()
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks = [{"id": f"t{i}", "page": None, "type": "body", "text": p}
              for i, p in enumerate(paras, 1)]
    return {"source": os.path.basename(path), "engine": "text", "page_count": None,
            "warnings": [], "blocks": blocks}


def build_blocks(material: str) -> tuple[dict, str]:
    if material.lower().endswith(".pdf"):
        return pdf_text.extract(material), "pdf_text"
    if material.lower().endswith(TEXT_SUFFIXES):
        return blocks_from_text(material), "text"
    return pdf_text.extract(material), "pdf_text"


# --------------------------------------------------------------------------- #
# field resolution
# --------------------------------------------------------------------------- #
def resolve(spec: dict, index: evidence_mod.LocatorIndex, source_id: str) -> tuple[dict, dict | None, dict | None]:
    """One field: (result entry, claim or None, uncertainty or None)."""
    fid = spec["field_id"]
    question = spec.get("question") or spec.get("instruction") or spec.get("prompt") or ""
    patterns = spec.get("regex") or spec.get("pattern") or spec.get("patterns")
    if isinstance(patterns, str):
        patterns = [patterns]

    if patterns:
        for pattern in patterns:
            try:
                rx = re.compile(pattern, re.I if spec.get("ignore_case") else 0)
            except re.error as exc:
                return ({"status": "not_reported", "value": None,
                         "method": f"pattern did not compile: {exc}"}, None,
                        {"field_id": fid, "reason": f"bad pattern in case: {exc}",
                         "needs_human_review": True})
            for block in index.blocks:
                text = " ".join(block.get("text", "").split())
                m = rx.search(text)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                loc = index._locator(block)
                entry = {"status": "answered", "value": value, "locator": loc,
                         "block_type": block.get("type")}
                claim = {"kind": fid, "evidence": {"locator": loc, "quote": m.group(0)},
                         "source_id": source_id, "location": loc}
                return entry, claim, None

    # No pattern, or it matched nothing: this is the common case for hidden material,
    # where the question is natural language and only an agent can answer it.
    if spec.get("absent_if_no_match"):
        return ({"status": "not_reported", "value": None,
                 "method": f"searched {len(index.blocks)} blocks, pattern did not occur"}, None,
                {"field_id": fid,
                 "reason": "no match anywhere in the extracted text; reported as not_reported",
                 "needs_human_review": False})

    reason = ("case supplied no mechanical pattern for this field; an agent must read the "
              "material and fill it with a verbatim quote" if not patterns else
              "case pattern did not match; wording may differ, needs human review")
    return ({"status": "not_reported", "value": None,
             "method": f"searched {len(index.blocks)} blocks, no answer produced"}, None,
            {"field_id": fid, "reason": reason, "question": question,
             "needs_human_review": True})


def assemble(case_id, fields, claims, uncertainties, materials, notes, unreadable=False) -> dict:
    answered = sum(1 for f in fields.values() if f.get("status") == "answered")
    if unreadable:
        # The material could not be opened at all. This is an input failure, not a reading
        # outcome, and saying "insufficient_evidence" would imply the material was read
        # and came up short.
        status = "input_error"
    elif fields and answered == len(fields):
        status = "answered"
    elif answered:
        status = "partial"
    else:
        status = "insufficient_evidence"

    limitations = []
    if unreadable:
        limitations.append("The case's material could not be resolved or opened; nothing was read.")
    if status != "answered":
        missing = [k for k, v in fields.items() if v.get("status") != "answered"]
        limitations.append(
            f"{len(missing)} of {len(fields)} fields are not answered: {', '.join(missing[:12])}"
            if fields else "the case named no resolvable target field; nothing could be answered")
        limitations.append(
            "Fields marked not_reported were searched for and not found, or had no mechanical "
            "pattern. They are not zero and were not imputed; a human should complete them.")
    limitations.extend(notes)

    result = {"case_id": case_id, "status": status, "result": fields,
              "claims": claims, "uncertainties": uncertainties, "limitations": limitations}
    # Mark example runs explicitly so contract.py does not flag the published example id.
    if isinstance(case_id, str) and "EXAMPLE" in case_id.upper():
        result["example_run"] = True
    return result


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_probe(a) -> int:
    case = load_case(a.case)
    materials = find_materials(case, a.case)
    specs = find_field_specs(case)
    print(json.dumps({
        "case_path": os.path.abspath(a.case),
        "case_id": case_id_of(case),
        "top_level_keys": sorted(case.keys()) if isinstance(case, dict) else None,
        "materials": materials,
        "field_count": len(specs),
        "field_ids": [s["field_id"] for s in specs],
        "fields_with_pattern": [s["field_id"] for s in specs
                                if s.get("regex") or s.get("pattern") or s.get("patterns")],
    }, ensure_ascii=False, indent=1))
    return 0 if case_id_of(case) else 2


def _prepare(a):
    case = load_case(a.case)
    case_id = case_id_of(case)
    os.makedirs(a.work, exist_ok=True)
    materials = find_materials(case, a.case)
    if not materials:
        return case_id, None, [], [], ["no material file resolved from the case"]
    payload, engine = build_blocks(materials[0])
    blocks_path = os.path.join(a.work, "blocks.json")
    json.dump(payload, open(blocks_path, "w", encoding="utf-8"), ensure_ascii=False)
    return case_id, blocks_path, materials, find_field_specs(case), [f"extraction engine: {engine}"]


def cmd_bootstrap(a) -> int:
    case_id, blocks_path, materials, specs, notes = _prepare(a)
    if blocks_path is None:
        result = assemble(case_id, {}, [], [{"field_id": None,
                                             "reason": "material missing: no file named by the "
                                                       "case could be found or opened",
                                             "needs_human_review": True}], [], notes,
                          unreadable=True)
    else:
        index = evidence_mod.LocatorIndex(json.load(open(blocks_path, encoding="utf-8")))
        source_id = os.path.basename(materials[0])
        fields, claims, uncertainties = {}, [], []
        for spec in specs:
            entry, claim, unc = resolve(spec, index, source_id)
            fields[spec["field_id"]] = entry
            if claim:
                claims.append(claim)
            if unc:
                uncertainties.append(unc)
        result = assemble(case_id, fields, claims, uncertainties, materials, notes)

    problems = contract_mod.check(result, "reading")["problems"]
    _write(result, a.out)
    print(json.dumps({"out": os.path.abspath(a.out), "case_id": case_id,
                      "status": result["status"], "fields": len(result["result"]),
                      "claims": len(result["claims"]),
                      "contract_problems": problems}, ensure_ascii=False))
    return 0 if not problems else 1


def cmd_attach(a) -> int:
    """Merge agent-supplied values, keeping only quotes that are verbatim present."""
    case_id, blocks_path, materials, specs, notes = _prepare(a)
    index = evidence_mod.LocatorIndex(json.load(open(blocks_path, encoding="utf-8")))
    supplied = json.load(open(a.values, encoding="utf-8"))
    supplied = supplied.get("result", supplied)
    by_id = {s["field_id"]: s for s in specs}

    fields, claims, uncertainties, rejected = {}, [], [], []
    for fid, item in supplied.items():
        item = item if isinstance(item, dict) else {"value": item}
        value = item.get("value")
        quote = item.get("quote") or item.get("evidence", {}).get("quote") if isinstance(
            item.get("evidence"), dict) else item.get("quote")
        if value in (None, "") or str(value).strip().lower() in ("", "not_reported", "unable"):
            fields[fid] = {"status": "not_reported", "value": None}
            uncertainties.append({"field_id": fid, "reason": "supplied as absent",
                                  "needs_human_review": False})
            continue
        if quote:
            check = evidence_mod.verify_quote(index, quote)
            if not check["found"]:
                rejected.append({"field_id": fid, "quote": quote,
                                 "reason": "quote not found in the material"})
                fields[fid] = {"status": "not_reported", "value": None,
                               "method": "supplied quote could not be located; value withheld"}
                uncertainties.append({"field_id": fid, "reason":
                                      "the supplied quote is not in the material, so the value "
                                      "was withheld rather than reported unsourced",
                                      "needs_human_review": True})
                continue
            loc = item.get("locator") or check["locator"]
            fields[fid] = {"status": "answered", "value": value, "locator": loc}
            claims.append({"kind": fid,
                           "evidence": {"locator": loc, "quote": quote},
                           "source_id": by_id.get(fid, {}).get("source_id")
                           or (os.path.basename(materials[0]) if materials else case_id),
                           "location": loc})
        else:
            fields[fid] = {"status": "not_reported", "value": None,
                           "method": "supplied without a quote; no evidence to bind to"}
            uncertainties.append({"field_id": fid,
                                  "reason": "value supplied without evidence; withheld",
                                  "needs_human_review": True})

    for spec in specs:
        if spec["field_id"] not in fields:
            entry, claim, unc = resolve(spec, index, os.path.basename(materials[0]) if materials else case_id)
            fields[spec["field_id"]] = entry
            if claim:
                claims.append(claim)
            if unc:
                uncertainties.append(unc)

    result = assemble(case_id, fields, claims, uncertainties, materials,
                      notes + [f"withheld {len(rejected)} unsupported value(s)"] if rejected else notes)
    if rejected:
        result["rejected_values"] = rejected
    problems = contract_mod.check(result, "reading")["problems"]
    _write(result, a.out)
    print(json.dumps({"out": os.path.abspath(a.out), "case_id": case_id,
                      "status": result["status"], "fields": len(fields),
                      "accepted": len(claims), "withheld": len(rejected),
                      "contract_problems": problems}, ensure_ascii=False))
    return 0 if not problems else 1


def _write(result: dict, out: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    # keep the previous file recoverable: a run that dies mid-write must not leave nothing
    if os.path.isfile(out):
        prev = os.path.join(os.path.dirname(os.path.abspath(out)), "result.prev.json")
        try:
            os.replace(out, prev)
        except OSError:
            pass
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a case end to end")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("--case", required=True); p.set_defaults(fn=cmd_probe)
    b = sub.add_parser("bootstrap")
    b.add_argument("--case", required=True)
    b.add_argument("--work", default="/app/work")
    b.add_argument("--out", required=True)
    b.set_defaults(fn=cmd_bootstrap)
    t = sub.add_parser("attach")
    t.add_argument("--case", required=True)
    t.add_argument("--work", default="/app/work")
    t.add_argument("--values", required=True)
    t.add_argument("--out", required=True)
    t.set_defaults(fn=cmd_attach)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
