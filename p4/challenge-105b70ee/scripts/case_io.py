#!/usr/bin/env python3
"""Read the case package the harness drops in `/input`, tolerate shape drift, and
emit a field scaffold that cannot silently lose a requested field.

The evaluator's files are the ground truth for *what to extract*:

  /input/case.json           - case_id, task_instruction, output_contract, source
  /input/target-fields.json  - the requested field_ids (organizer revision v2)

Neither file's exact shape is guaranteed. This module accepts the shapes seen in
public examples plus the plausible variants, records which one it found, and
never invents a field_id it cannot point at in the input.

CLI:
  python3 case_io.py read --case /input/case.json --targets /input/target-fields.json \
      --outdir /app/work [--material-dir /input]

Writes:
  <outdir>/case_context.json     normalized case + target fields + material candidates
  <outdir>/fields_scaffold.json  one record per requested field_id, status="pending"
  <outdir>/material_index.json   every readable material file with size/kind

Exit codes:
  0  case read, every requested field_id scaffolded
  1  case file missing/unparseable, or zero requested fields could be recovered
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

FIELD_ID_KEYS = ("field_id", "fieldId", "id", "key", "name", "slug")
FIELD_NAME_KEYS = ("label", "title", "display_name", "description", "question", "definition")
FIELD_UNIT_KEYS = ("units", "unit", "uom", "unit_expected", "units_expected",
                   "expected_units", "expected_unit")
FIELD_ALIAS_KEYS = ("aliases", "synonyms", "search_terms", "keywords", "terms")
TARGET_CONTAINERS = ("target_fields", "targetFields", "fields", "field_specs", "fieldSpecs",
                     "targets", "field_ids", "fieldIds", "requested_fields", "requestedFields",
                     "items", "data")
CASE_ID_KEYS = ("case_id", "caseId", "id", "case", "slug")
MATERIAL_KEYS = ("material", "materials", "paper", "papers", "pdf", "pdfs", "document",
                 "documents", "files", "sources", "attachments", "artifact", "artifacts")
MATERIAL_EXTS = (".pdf", ".txt", ".md", ".json", ".xml", ".html", ".docx", ".doc",
                 ".pptx", ".csv", ".tsv", ".xlsx", ".tex")
# The harness's own control files sit next to the material and would otherwise be
# listed as "materials". The target list is the request, not the source: counting
# it as material would let a run claim it read a paper it never opened.
CONTROL_FILES = ("case.json", "target-fields.json", "target_fields.json", "case_context.json",
                 "output_contract.json", "manifest.json", "task.json")


def load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _first(mapping: dict, keys) -> object | None:
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        if mapping.get(k) not in (None, "", [], {}):
            return mapping[k]
    return None


def _dig(mapping: dict, *paths):
    """Look for case_id one level down in common wrappers."""
    for path in paths:
        node = mapping
        ok = True
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok and node not in (None, "", [], {}):
            return node
    return None


def find_case_id(case: dict) -> tuple[str | None, str]:
    """Return (case_id, where_it_came_from). Never synthesizes an id."""
    if not isinstance(case, dict):
        return None, "none"
    direct = _first(case, CASE_ID_KEYS)
    if isinstance(direct, str):
        return direct, "top-level"
    nested = _dig(case, "case.case_id", "case.id", "meta.case_id", "meta.id",
                  "task.case_id", "input.case_id", "source.case_id")
    if isinstance(nested, str):
        return nested, "nested"
    return None, "none"


def _as_field_list(payload) -> tuple[list[dict], str]:
    """Normalize any plausible target-fields payload into [{field_id, ...}]."""
    if payload is None:
        return [], "absent"
    if isinstance(payload, list):
        return _normalize_items(payload), "list"
    if isinstance(payload, dict):
        for key in TARGET_CONTAINERS:
            inner = payload.get(key)
            if isinstance(inner, list):
                return _normalize_items(inner), f"dict.{key}[list]"
            if isinstance(inner, dict):
                return _normalize_items(inner), f"dict.{key}[map]"
        # a bare map field_id -> spec
        if any(isinstance(v, (dict, str)) for v in payload.values()):
            return _normalize_items(payload), "bare-map"
    return [], "unrecognized"


def _normalize_items(items) -> list[dict]:
    out = []
    if isinstance(items, dict):
        iterable = [{"field_id": k, **(v if isinstance(v, dict) else {"_value": v})}
                    for k, v in items.items()]
    else:
        iterable = list(items)
    for raw in iterable:
        if isinstance(raw, str):
            out.append({"field_id": raw.strip(), "label": None, "units_expected": None,
                        "aliases": [], "spec_raw": raw})
            continue
        if not isinstance(raw, dict):
            continue
        fid = _first(raw, FIELD_ID_KEYS)
        if not isinstance(fid, str) or not fid.strip():
            continue
        aliases = _first(raw, FIELD_ALIAS_KEYS) or []
        if isinstance(aliases, str):
            aliases = [aliases]
        units = _first(raw, FIELD_UNIT_KEYS)
        out.append({
            "field_id": fid.strip(),
            "label": _first(raw, FIELD_NAME_KEYS),
            "units_expected": units if isinstance(units, str) else None,
            "aliases": [a for a in aliases if isinstance(a, str)],
            "spec_raw": raw,
        })
    # de-duplicate, preserving first-seen order: a repeated field_id would
    # otherwise produce two rows and a silent "extra field" report later.
    seen, uniq = set(), []
    for f in out:
        if f["field_id"] in seen:
            continue
        seen.add(f["field_id"])
        uniq.append(f)
    return uniq


def discover_materials(case: dict, material_dir: str | None) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def add(path: str, origin: str):
        if not path:
            return
        ap = path if os.path.isabs(path) else os.path.abspath(path)
        if os.path.basename(ap).lower() in CONTROL_FILES:
            return
        if ap in seen or not os.path.isfile(ap):
            return
        seen.add(ap)
        found.append({"path": ap, "origin": origin,
                      "bytes": os.path.getsize(ap),
                      "kind": os.path.splitext(ap)[1].lower().lstrip(".") or "unknown"})

    def walk(node, origin: str):
        if isinstance(node, str):
            if os.path.splitext(node)[1].lower() in MATERIAL_EXTS:
                add(node, origin)
        elif isinstance(node, list):
            for item in node:
                walk(item, origin)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in MATERIAL_KEYS or k in ("path", "file", "url", "local_path"):
                    walk(v, f"{origin}.{k}")

    if isinstance(case, dict):
        for key in MATERIAL_KEYS:
            if key in case:
                walk(case[key], key)
    if material_dir and os.path.isdir(material_dir):
        for name in sorted(os.listdir(material_dir)):
            ext = os.path.splitext(name)[1].lower()
            if ext in MATERIAL_EXTS:
                add(os.path.join(material_dir, name), "listing")
        for pattern in ("**/*.pdf", "**/*.txt"):
            for p in sorted(glob.glob(os.path.join(material_dir, pattern), recursive=True))[:20]:
                add(p, "glob")
    return found


def scaffold(fields: list[dict], origin: str) -> dict:
    return {
        "fields_origin": origin,
        "note": ("status=pending is NOT a deliverable status; fill every record and re-run "
                 "fields_check.py until no record is pending"),
        "fields": [{
            "field_id": f["field_id"],
            "field_label": f.get("label"),
            "units_expected": f.get("units_expected"),
            "aliases": f.get("aliases") or [],
            "scalar": None,
            "units": None,
            "statistic": None,
            "condition": None,
            "comparator": None,
            "status": "pending",
            "paragraph_id": None,
            "quote": None,
            "semantic_check": None,
        } for f in fields],
    }


def derive_fields(case: dict) -> list[dict]:
    """Last resort when no target-fields file exists.

    Fields are derived from the task text and clearly labelled agent_derived so a
    reviewer is never told these were the organizer's requested fields.
    """
    text = " ".join(str(case.get(k) or "") for k in ("task_instruction", "task_specific_interface",
                                                     "task", "instruction"))
    import re
    ids = re.findall(r"\bfield_id\s*[:=]\s*[\"']?([A-Za-z0-9_.\-]{3,60})", text)
    out, seen = [], set()
    for fid in ids:
        if fid not in seen:
            seen.add(fid)
            out.append({"field_id": fid, "label": None, "units_expected": None,
                        "aliases": [], "spec_raw": {"derived": True}})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Read the case package and scaffold requested fields")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--case", default="/input/case.json")
    r.add_argument("--targets", default="/input/target-fields.json")
    r.add_argument("--outdir", default="/app/work")
    r.add_argument("--material-dir", default=None)
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    warnings: list[str] = []

    case, case_path_used = {}, None
    for path in (a.case, os.path.join(os.path.dirname(a.case) or ".", "case.json")):
        if path and os.path.isfile(path):
            try:
                case = load_json(path)
                case_path_used = os.path.abspath(path)
                break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"case file unparseable at {path}: {exc}")
    if not isinstance(case, dict):
        warnings.append("case payload is not an object; treating as empty")
        case = {}
    if case_path_used is None:
        warnings.append(f"no case file found at {a.case}")

    case_id, case_id_source = find_case_id(case)
    if case_id is None:
        warnings.append("case_id not present in the case payload: copy it verbatim once found; "
                        "if it truly is not supplied, report status=input_error rather than guessing")

    targets_payload, targets_path_used, targets_parse_error = None, None, None
    for path in (a.targets, os.path.join(os.path.dirname(a.case) or ".", "target-fields.json"),
                 "/input/target-fields.json"):
        if path and os.path.isfile(path):
            try:
                targets_payload = load_json(path)
                targets_path_used = os.path.abspath(path)
                break
            except Exception as exc:  # noqa: BLE001
                targets_parse_error = str(exc)
                warnings.append(f"target-fields unparseable at {path}: {exc}")

    fields, shape = _as_field_list(targets_payload)
    origin = "organizer_target_fields"
    if not fields:
        derived = derive_fields(case)
        if derived:
            fields, origin, shape = derived, "agent_derived", "derived-from-task-text"
            warnings.append("no usable target-fields payload: fields were DERIVED from the task "
                            "text and are labelled agent_derived; treat coverage as unverified")
        else:
            origin, shape = "unavailable", "absent"
            warnings.append("no target-fields payload and nothing derivable: every downstream "
                            "coverage claim is unverifiable; report status=partial or "
                            "insufficient_evidence and say the field list was not supplied")

    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    if not contract:
        warnings.append("no output_contract in the case payload: fall back to the reading family "
                        "in references/output-contract.md and say so in limitations")

    ctx = {
        "case_path": case_path_used,
        "case_id": case_id,
        "case_id_source": case_id_source,
        "task_instruction": case.get("task_instruction"),
        "task_specific_interface": case.get("task_specific_interface"),
        "organizer_interface_revision": case.get("organizer_interface_revision"),
        "output_contract": contract,
        "output_contract_keys": list(contract),
        "source": case.get("source"),
        "targets_path": targets_path_used,
        "targets_shape": shape,
        "targets_parse_error": targets_parse_error,
        "fields_origin": origin,
        "field_count": len(fields),
        "field_ids": [f["field_id"] for f in fields],
        "materials": discover_materials(case, a.material_dir or os.path.dirname(a.case) or None),
        "warnings": warnings,
    }
    json.dump(ctx, open(os.path.join(a.outdir, "case_context.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(scaffold(fields, origin),
              open(os.path.join(a.outdir, "fields_scaffold.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"materials": ctx["materials"]},
              open(os.path.join(a.outdir, "material_index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(json.dumps({"case_id": case_id, "fields_origin": origin, "field_count": len(fields),
                      "materials": [m["path"] for m in ctx["materials"]],
                      "warnings": warnings}, ensure_ascii=False, indent=1))
    # The exit code must match the docstring: a missing case payload is a real
    # failure even when the target-fields file parsed, because case_id and
    # output_contract cannot then be honoured.
    if case_path_used is None:
        return 1
    return 0 if fields else 1


if __name__ == "__main__":
    sys.exit(main())
