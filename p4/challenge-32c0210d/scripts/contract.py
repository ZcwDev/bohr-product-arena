#!/usr/bin/env python3
"""Validate a case `result.json` against the task's published output contract.

The platform publishes, per challenge, an `output_contract` (see the task's
`environment/resources/example-input.json`). Three families occur:

  reading  : case_id, status, result, claims, uncertainties
  writing  : case_id, deliverable, preserved_facts, evidence_map, change_log
  artifact : case_id, status, editable_source, export_artifact, content_map,
             compliance_checks, exceptions

This checker is deliberately mechanical: it does not judge scientific quality.
It answers one question only - "would a reviewer looking for the contract
fields find them, with the right shape and honest enums?"

CLI:
  python3 contract.py check  result.json [--family reading|writing|artifact] [--contract path.json]
  python3 contract.py check  result.json --blocks blocks.json   # resolve every locator, verify quotes
  python3 contract.py stub   --family reading -o result.json
  python3 contract.py merge  result.json part.json      # deep-merge a patch into result.json

Exit code 0 when the file satisfies the family contract, 1 otherwise.

A field being *present* is not the same as the field doing its job. For the
writing family the contract says `evidence_map` is "关键论断到来源定位的映射" and
`preserved_facts` is "数字、条件、术语和来源不可凭空改变", so an entry without a
locator or without a source does not satisfy it - it just looks like it does.
The entry-level rules below exist because a result can carry all five keys and
still be unciteable.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Per-entry requirements. `None` = the key must be present and non-empty.
FAMILIES = {
    "reading": {
        "required": ["case_id", "status", "result", "claims", "uncertainties"],
        "enum": {"status": ["answered", "partial", "insufficient_evidence", "input_error"]},
        "list_of_obj": ["claims"],
        "claim_keys": ["kind", "evidence"],
        "entry_required": {},
        # claims may be honestly unsupported; those must carry an explicit marker
        "honesty_fields": ("status", "verdict"),
        "locator_fields": {"claims": ("evidence",)},
    },
    "writing": {
        "required": ["case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"],
        # `status` is an extension in this skill's own docs, not in the published
        # contract; accept it, and if present require the reading-family enum.
        "enum": {"status": ["answered", "partial", "insufficient_evidence", "input_error"]},
        "list_of_obj": ["preserved_facts", "evidence_map", "change_log"],
        "claim_keys": [],
        "entry_required": {
            "preserved_facts": ["fact", "source", "changed"],
            "evidence_map": ["claim", "locator", "quote"],
            "change_log": ["action", "reason"],
        },
        "honesty_fields": ("status", "verdict", "evidence_status"),
        "locator_fields": {"evidence_map": ("locator",), "preserved_facts": ("source",)},
    },
    "artifact": {
        "required": ["case_id", "status", "editable_source", "export_artifact",
                     "content_map", "compliance_checks", "exceptions"],
        "enum": {"status": ["ok", "partial", "unable", "input_error"]},
        "list_of_obj": ["content_map", "compliance_checks"],
        "claim_keys": [],
        "entry_required": {},
        "honesty_fields": ("status", "verdict"),
        "locator_fields": {"content_map": ("locator",)},
    },
}

# Honesty markers that make an entry legitimately unciteable - provided the entry
# says *why*. Anything else missing a locator is a defect, not a finding.
HONESTY = {"not_reported", "unable", "conflict", "unresolved", "insufficient_evidence"}

STATUS_ALIASES = {
    "ok": "answered", "success": "answered", "complete": "answered", "done": "answered",
    "completed": "answered", "partial_result": "partial", "unable": "insufficient_evidence",
    "refused": "insufficient_evidence", "not_found": "insufficient_evidence",
    "error": "input_error", "failed": "input_error", "unreadable": "input_error",
}

PLACEHOLDER = {"", "-", "n/a", "na", "none", "null", "tbd", "todo", "?", "unknown",
               "待补充", "未知", "无"}


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in PLACEHOLDER
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _entry_verdict(item: dict, spec: dict) -> str | None:
    """Return an honesty marker if the entry openly declares itself unsupported."""
    for field in spec.get("honesty_fields", ()):
        value = item.get(field)
        if isinstance(value, str) and value.strip().lower() in HONESTY:
            return value.strip().lower()
        if isinstance(value, list):
            for v in value:
                if isinstance(v, str) and v.strip().lower() in HONESTY:
                    return v.strip().lower()
    return None


def check_entries(result: dict, spec: dict, family: str,
                  index_args: dict | None = None,
                  root: str = ".") -> tuple[list[str], list[str]]:
    """Entry-level and locator-level checks for the structured list fields.

    A list of objects that all carry the right keys can still be useless: an
    `evidence_map` entry whose `locator` resolves to nothing is worse than one
    that says `not_reported`, because it looks checkable and is not.
    """
    problems: list[str] = []
    notes: list[str] = []
    required_by_list = spec.get("entry_required") or {}
    locator_by_list = spec.get("locator_fields") or {}

    for key, fields in required_by_list.items():
        value = result.get(key)
        if not isinstance(value, list):
            continue
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            marked = _entry_verdict(item, spec)
            for field in fields:
                if _is_empty(item.get(field)):
                    if marked:
                        notes.append(f"{key}[{i}] {field!r} absent but entry is marked {marked!r}")
                    else:
                        problems.append(
                            f"{key}[{i}] missing {field!r}: {_why_field(family, key, field)}")
            # honesty must carry a reason, otherwise it is just a shrug
            if marked and _is_empty(item.get("why") or item.get("reason") or item.get("note")
                                    or item.get("limitation")):
                notes.append(f"{key}[{i}] marked {marked!r} without a stated reason")

    if index_args:
        loc_problems, loc_notes = _resolve_locators(result, spec, index_args, root)
        problems.extend(loc_problems)
        notes.extend(loc_notes)
    return problems, notes


def _why_field(family: str, key: str, field: str) -> str:
    table = {
        ("writing", "evidence_map", "locator"): "the contract maps a claim to a source location",
        ("writing", "evidence_map", "quote"): "a locator without the text it points at cannot be re-checked",
        ("writing", "evidence_map", "claim"): "an entry with no claim maps nothing",
        ("writing", "preserved_facts", "source"): "an unchanged fact with no source is an assertion, not a preservation",
        ("writing", "preserved_facts", "changed"): "state whether the fact was altered; silence is not 'no'",
        ("writing", "preserved_facts", "fact"): "no fact recorded",
        ("writing", "change_log", "action"): "a change log entry must name the action",
        ("writing", "change_log", "reason"): "an unexplained edit is an un-reviewable edit",
    }
    return table.get((family, key, field), f"{field!r} is required by the contract")


def _parse_file_locator(loc: str) -> tuple[str, str] | None:
    """Split `path#json.key` / `path:123` / `path:para` into (path, anchor).

    Not every citable fact lives in a manuscript paragraph. A case's own
    declarations (ids, versions, workorder targets) are citable too, and saying
    "unsupported" about them would be as wrong as inventing a page number.
    """
    if not isinstance(loc, str) or " " in loc.strip():
        return None
    if "#" in loc:
        path, anchor = loc.split("#", 1)
        return (path, anchor) if path else None
    hit = re.match(r"^(?P<path>[^:]+?\.(?:json|md|txt|ya?ml|py|csv|tsv|html)):(?P<anchor>.+)$", loc)
    if hit:
        return hit.group("path"), hit.group("anchor")
    return None


def _file_locator_resolves(loc: str, root: str) -> tuple[bool, str]:
    """True when the file exists and the anchor names something inside it."""
    parts = _parse_file_locator(loc)
    if parts is None:
        return False, "not a file locator"
    path, anchor = parts
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.isfile(full):
        return False, f"{path!r} does not exist"
    if path.lower().endswith(".json"):
        try:
            data = json.load(open(full, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return False, f"cannot parse {path!r} as json: {exc}"
        node = data
        for step in anchor.split("."):
            if isinstance(node, dict) and step in node:
                node = node[step]
            elif isinstance(node, list) and step.isdigit() and int(step) < len(node):
                node = node[int(step)]
            else:
                return False, f"anchor {anchor!r} not present in {path!r}"
        return True, ""
    return True, ""


def _file_quote_resolves(quote: str, loc: str, root: str):
    """True/False when checkable, else a short reason string.

    A note is not a pass. Only a verbatim hit inside the cited file counts as
    True; anything we could not read comes back as a reason, never as silence.
    """
    parts = _parse_file_locator(loc)
    if parts is None:
        return "not a file locator"
    path, _anchor = parts
    full = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        blob = open(full, encoding="utf-8", errors="replace").read()
    except Exception as exc:  # noqa: BLE001
        return f"cannot read {path!r}: {exc}"
    if path.lower().endswith(".json"):
        # search the raw text so JSON escaping cannot hide a real quote
        pass
    if quote.strip() in blob or " ".join(quote.split()) in " ".join(blob.split()):
        return True
    return False


def _resolve_locators(result: dict, spec: dict, index_args: dict,
                      root: str = ".") -> tuple[list[str], list[str]]:
    """Every locator in the result must resolve inside the ingested blocks."""
    problems: list[str] = []
    notes: list[str] = []
    blocks_path = index_args.get("blocks")
    if not blocks_path or not os.path.isfile(blocks_path):
        if blocks_path:
            return [f"--blocks {blocks_path!r} not found: locators were NOT resolved"], notes
        return problems, notes
    try:
        import evidence  # same directory
    except Exception as exc:  # noqa: BLE001
        return [f"cannot import evidence.py to verify locators: {exc}"], notes
    payload = json.load(open(blocks_path, encoding="utf-8"))
    blocks = payload.get("blocks") or []
    if not blocks:
        # An empty index is an honest state -- the pack can ship no manuscript at
        # all -- not a defect in the result. It only becomes one when the result
        # cites a manuscript paragraph anyway: every such locator has nothing to
        # resolve against and is reported individually below. Failing the whole
        # result here would instead punish the answer that says so plainly.
        notes.append(f"blocks file {blocks_path!r} has no blocks (status "
                     f"{payload.get('status')!r}); only file anchors can be resolved")
        index = None
        known: set = set()
    else:
        index = evidence.LocatorIndex(payload)
        known = {b.get("id") for b in blocks} | {b.get("locator") for b in blocks}

    for key, fields in (spec.get("locator_fields") or {}).items():
        value = result.get(key)
        if not isinstance(value, list):
            continue
        for i, item in enumerate(value):
            if not isinstance(item, dict) or _entry_verdict(item, spec):
                continue
            # A locator pointing at a case file is checked against that file; the
            # manuscript index would only ever report it as absent.
            quote_checked_at_file = False
            for field in fields:
                loc = item.get(field)
                if _is_empty(loc):
                    continue
                if loc in known:
                    continue
                # A fact can also be grounded in the case's own declarations
                # (ids, versions, workorder targets), not only in manuscript text.
                ok, why = _file_locator_resolves(str(loc), root)
                if ok:
                    notes.append(f"{key}[{i}].{field} {loc!r} is a file anchor, not a manuscript block")
                    q = item.get("quote")
                    if isinstance(q, str) and q.strip():
                        hit = _file_quote_resolves(q, str(loc), root)
                        if hit is True:
                            notes.append(f"{key}[{i}].quote is verbatim in {loc!r}")
                            quote_checked_at_file = True
                        elif hit is False:
                            problems.append(f"{key}[{i}].quote is not verbatim in {loc!r}; "
                                            f"a quotation that had to be altered is not evidence")
                            quote_checked_at_file = True
                        else:
                            notes.append(f"{key}[{i}].quote could not be checked in {loc!r}: {hit}")
                            quote_checked_at_file = True
                    continue
                detail = f" ({why})" if _parse_file_locator(str(loc)) else ""
                problems.append(f"{key}[{i}].{field} {loc!r} does not resolve to any ingested block"
                                f"{detail}")
            if quote_checked_at_file:
                continue
            quote = item.get("quote")
            if isinstance(quote, str) and quote.strip():
                if index is None:
                    problems.append(f"{key}[{i}].quote cannot be checked against the material: "
                                    f"no manuscript was ingested, so a manuscript quotation has "
                                    f"nothing to be verified against")
                    continue
                res = evidence.verify_quote(index, quote)
                if not res.get("found"):
                    problems.append(f"{key}[{i}].quote is not present in the material "
                                    f"(locator {item.get('locator')!r})")
                elif not res.get("exact"):
                    span = _quote_span(blocks, evidence, quote)
                    if span:
                        # legitimately continues into the next block (page/paragraph break)
                        notes.append(f"{key}[{i}].quote is verbatim only when blocks are joined: "
                                     f"{span[0]}..{span[-1]}; cite the range, not a single locator")
                    else:
                        # an approximate hit means text was edited, not just re-wrapped
                        problems.append(f"{key}[{i}].quote is not verbatim at {item.get('locator')!r}"
                                        f"; a quotation that had to be altered is not evidence")
                elif res.get("locator") != item.get("locator"):
                    problems.append(f"{key}[{i}].quote occurs at {res.get('locator')!r}, "
                                    f"not at the stated {item.get('locator')!r}")
    return problems, notes


def _quote_span(blocks: list, evidence_module, quote: str) -> list[str] | None:
    """Ids of consecutive blocks whose joined text contains the quote verbatim."""
    needle = evidence_module.normalize(quote).lower()
    texts = [evidence_module.normalize(b.get("text") or "").lower() for b in blocks]
    for start in range(len(blocks)):
        joined = ""
        for end in range(start, min(start + 6, len(blocks))):
            joined = (joined + " " + texts[end]).strip()
            if needle in joined:
                return [blocks[k].get("id") for k in range(start, end + 1)]
            if len(joined) > len(needle) + 400:
                break
    return None


def check(result: dict, family: str | None = None, contract: dict | None = None,
          index_args: dict | None = None, root: str = ".") -> dict:
    problems: list[str] = []
    notes: list[str] = []
    if family is None:
        family = guess_family(result)
    spec = FAMILIES.get(family)
    if spec is None:
        return {"family": None, "problems": [f"unknown family {family!r}"], "notes": []}

    required = list(spec["required"])
    if contract:
        for key in contract:
            if key not in required:
                required.append(key)

    for key in required:
        if key not in result:
            problems.append(f"missing top-level field {key!r}")
        elif _is_empty(result[key]) and key not in ("exceptions", "uncertainties"):
            problems.append(f"field {key!r} is empty; an absent answer is not an answer")

    status = result.get("status")
    if status is not None:
        allowed = spec["enum"].get("status") or []
        if allowed and status not in allowed:
            problems.append(f"status {status!r} not one of {allowed}")
        if _is_empty(status):
            problems.append("status present but empty")

    for key in spec["list_of_obj"]:
        value = result.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            problems.append(f"{key!r} must be a list, got {type(value).__name__}")
            continue
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                problems.append(f"{key}[{i}] must be an object, got {type(item).__name__}")
                continue
            if key == "claims":
                for ck in spec["claim_keys"]:
                    if _is_empty(item.get(ck)):
                        problems.append(f"{key}[{i}] missing {ck!r}")

    entry_problems, entry_notes = check_entries(result, spec, family, index_args, root)
    problems.extend(entry_problems)
    notes.extend(entry_notes)

    if not _is_empty(result.get("case_id")) and result.get("case_id") == "EXAMPLE-NOT-A-DEMO-CASE":
        # legitimate when the run really is on the published interface example, which the task
        # itself ships with that id; only flag it when nothing says so.
        declares_example = bool(result.get("example_run") or result.get("deliverable_kind")
                                or "example" in json.dumps(result.get("limitations") or [], ensure_ascii=False).lower())
        if not declares_example:
            problems.append("case_id is the published example id but nothing in the result says the run "
                            "was on the example; add example_run=true or copy the real case_id verbatim")

    if status in ("partial", "insufficient_evidence", "unable", "refused"):
        limit = result.get("limitations") or result.get("uncertainties")
        if _is_empty(limit):
            notes.append("non-ok status without limitations/uncertainties: state what is missing "
                         "and where a human should take over")

    if result.get("status") == "answered":
        claims = result.get("claims")
        if isinstance(claims, list) and claims and all(_is_empty(c.get("evidence")) for c in claims
                                                       if isinstance(c, dict)):
            problems.append("status=answered but no claim carries evidence")

    return {"family": family, "required": required, "problems": problems, "notes": notes}


def guess_family(result: dict) -> str:
    keys = set(result)
    if {"deliverable", "preserved_facts"} & keys:
        return "writing"
    if {"editable_source", "export_artifact"} & keys:
        return "artifact"
    return "reading"


def normalize(result: dict) -> dict:
    """Repair only mechanical defects; never invent content."""
    out = copy.deepcopy(result)
    status = out.get("status")
    if isinstance(status, str):
        key = status.strip().lower()
        if key in STATUS_ALIASES:
            if out.get("family") == "artifact" or "editable_source" in out:
                target = {"answered": "ok", "insufficient_evidence": "unable",
                          "input_error": "input_error", "partial": "partial"}.get(
                              STATUS_ALIASES[key], "ok")
            else:
                target = STATUS_ALIASES[key]
            out["status"] = target
    for key in ("claims", "change_log", "content_map", "compliance_checks"):
        if isinstance(out.get(key), dict):
            out[key] = [{"key": k, "value": v} for k, v in out[key].items()]
    return out


def stub(family: str) -> dict:
    base = {"case_id": "", "status": "partial", "limitations": []}
    if family == "reading":
        base.update({"result": {}, "claims": [], "uncertainties": []})
    elif family == "writing":
        base = {"case_id": "", "deliverable": {}, "preserved_facts": [],
                "evidence_map": [], "change_log": [],
                "status": "partial", "limitations": []}
    else:
        base.update({"editable_source": {}, "export_artifact": {}, "content_map": [],
                     "compliance_checks": [], "exceptions": []})
    return base


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a case result.json against its output contract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("result")
    c.add_argument("--family", default=None)
    c.add_argument("--contract", default=None, help="JSON file holding the published output_contract")
    c.add_argument("--blocks", default=None,
                   help="blocks.json from ingest.py; resolves every locator and verifies every quote")
    c.add_argument("--root", default=None,
                   help="directory that file locators (path#anchor) are relative to; "
                        "defaults to the result file's directory")
    c.add_argument("--normalize", action="store_true", help="rewrite safe mechanical fixes in place")
    s = sub.add_parser("stub")
    s.add_argument("--family", default="reading")
    s.add_argument("-o", "--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("result")
    m.add_argument("patch")
    a = ap.parse_args()

    if a.cmd == "stub":
        json.dump(stub(a.family), open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(a.out)
        return 0
    if a.cmd == "merge":
        base = json.load(open(a.result, encoding="utf-8"))
        patch = json.load(open(a.patch, encoding="utf-8"))
        json.dump(deep_merge(base, patch), open(a.result, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("merged", a.patch, "->", a.result)
        return 0

    raw = json.load(open(a.result, encoding="utf-8"))
    if a.normalize:
        fixed = normalize(raw)
        if fixed != raw:
            json.dump(fixed, open(a.result, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            raw = fixed
    contract = json.load(open(a.contract, encoding="utf-8")) if a.contract else None
    index_args = {"blocks": a.blocks} if a.blocks else None
    root = a.root or os.path.dirname(os.path.abspath(a.result))
    report = check(raw, a.family, contract, index_args, root)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
