#!/usr/bin/env python3
"""Check that a response letter, the change log and the revised manuscript agree.

Four questions, all mechanical:

  * coverage   - every reviewer comment id (R1.1, R1.2, ...) appearing in the
                 letter has a change_log entry, and vice versa;
  * landing    - every change_log entry that claims an edit (`action` not a
                 decline) quotes an `after` snippet that is really present in
                 the revised manuscript file;
  * locators   - every claimed edit carries a locator, and that locator resolves
                 in blocks.json when one is supplied;
  * honesty    - an entry with no locator/after snippet must say so
                 (status not_reported / unable / conflict) instead of being
                 silently partial.

Nothing here rewrites the deliverable. A failure is reported, never repaired.

CLI:
  python3 consistency.py check result.json [--letter letter.md] [--revised draft.md]
                                           [--blocks blocks.json] [--comments comments.json]
Exit code 0 when consistent, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ID_RE = re.compile(r"\bR(\d+)\.(\d+)\b")
ABSENT_RE = re.compile(r"^(?:not_reported|unable|conflict|n/?a|none|-|—|未报告|无法判断|材料冲突)$", re.I)

DECLINE_ACTIONS = {"none", "no_change", "decline", "disagree", "reject", "not_applicable"}
HONEST_STATUSES = {"not_reported", "unable", "conflict", "未报告", "无法判断", "材料冲突"}


def _norm(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def ids_in(text: str) -> list[str]:
    seen: list[str] = []
    for m in ID_RE.finditer(text):
        cid = f"R{m.group(1)}.{m.group(2)}"
        if cid not in seen:
            seen.append(cid)
    return seen


def entry_id(entry: dict) -> str | None:
    for key in ("comment_id", "reviewer_comment", "comment", "id", "ref"):
        raw = entry.get(key)
        if isinstance(raw, str):
            m = ID_RE.search(raw)
            if m:
                return f"R{m.group(1)}.{m.group(2)}"
            if raw.strip().upper().startswith("R"):
                return raw.strip().upper()
    blob = json.dumps(entry, ensure_ascii=False)
    m = ID_RE.search(blob)
    return f"R{m.group(1)}.{m.group(2)}" if m else None


def _status_of(entry: dict) -> str:
    for key in ("status", "verdict", "outcome"):
        value = entry.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _locators_of(entry: dict) -> list[str]:
    out = []
    for key, value in entry.items():
        if key in ("locator", "location", "source_locator", "locators") and value:
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, list):
                out.extend([v for v in value if isinstance(v, str)])
    return [x for x in out if x.strip()]


def check(result: dict, letter_text: str | None, revised_text: str | None,
          block_locators: set[str] | None) -> dict:
    problems: list[str] = []
    notes: list[str] = []
    change_log = result.get("change_log") or []
    if not isinstance(change_log, list):
        return {"problems": ["change_log must be a list"], "notes": []}

    letter_ids = ids_in(letter_text) if letter_text else []
    log_ids: list[str] = []
    for entry in change_log:
        if not isinstance(entry, dict):
            continue
        cid = entry_id(entry)
        if cid and cid not in log_ids:
            log_ids.append(cid)

    if letter_text:
        for cid in letter_ids:
            if cid not in log_ids:
                problems.append(f"{cid} appears in the letter but has no change_log entry")
    else:
        notes.append("no --letter supplied: cannot test letter -> change_log coverage")
    for cid in log_ids:
        if letter_text and cid not in letter_ids:
            problems.append(f"{cid} has a change_log entry but never appears in the letter")

    landed = 0
    edit_entries = 0
    for i, entry in enumerate(change_log):
        if not isinstance(entry, dict):
            problems.append(f"change_log[{i}] must be an object")
            continue
        label = entry_id(entry) or f"change_log[{i}]"
        action = str(entry.get("action", "")).strip().lower()
        status = _status_of(entry)
        is_decline = action in DECLINE_ACTIONS or status.lower() in HONEST_STATUSES

        after = entry.get("after") or entry.get("new_text") or entry.get("revised")
        locators = _locators_of(entry)

        if is_decline:
            if not str(entry.get("reason") or "").strip():
                problems.append(f"{label}: declined/not-reported without a reason")
            continue

        edit_entries += 1
        if not locators:
            problems.append(f"{label}: claims an edit but carries no locator")
        elif block_locators is not None:
            for loc in locators:
                if ABSENT_RE.match(loc.strip()):
                    continue
                if loc.strip() not in block_locators:
                    problems.append(f"{label}: locator {loc!r} does not resolve in blocks.json")

        if isinstance(after, str) and after.strip():
            if revised_text is None:
                notes.append(f"{label}: an 'after' snippet is present but no --revised file was "
                             f"supplied, so landing was not verified")
            elif _norm(after) in _norm(revised_text):
                landed += 1
            else:
                problems.append(f"{label}: 'after' snippet is not present in the revised manuscript "
                                f"- the edit did not land")
        else:
            problems.append(f"{label}: claims an edit but quotes no 'after' snippet, "
                            f"so the edit cannot be located in the revised draft")

    return {
        "problems": problems,
        "notes": notes,
        "comment_ids_in_letter": letter_ids,
        "comment_ids_in_change_log": log_ids,
        "edits_claimed": edit_entries,
        "edits_verified_in_revised": landed if revised_text is not None else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Letter / change_log / revised draft consistency")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("result")
    c.add_argument("--letter")
    c.add_argument("--revised")
    c.add_argument("--blocks")
    c.add_argument("--comments")
    a = ap.parse_args()

    result = json.load(open(a.result, encoding="utf-8"))
    letter_text = None
    if a.letter:
        if not os.path.isfile(a.letter):
            print(json.dumps({"problems": [f"letter not found: {a.letter}"]}, ensure_ascii=False, indent=1))
            return 1
        letter_text = open(a.letter, encoding="utf-8").read()
    revised_text = None
    if a.revised:
        if not os.path.isfile(a.revised):
            print(json.dumps({"problems": [f"revised manuscript not found: {a.revised}"]},
                             ensure_ascii=False, indent=1))
            return 1
        revised_text = open(a.revised, encoding="utf-8").read()

    block_locators = None
    if a.blocks and os.path.isfile(a.blocks):
        payload = json.load(open(a.blocks, encoding="utf-8"))
        blocks = payload.get("blocks") if isinstance(payload, dict) else payload
        block_locators = set()
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            loc = b.get("locator")
            if loc:
                block_locators.add(loc)
            elif b.get("page") is not None and (b.get("id") or b.get("block_id")) is not None:
                block_locators.add(f"p{b['page']}b{b.get('id') or b.get('block_id')}")

    report = check(result, letter_text, revised_text, block_locators)

    if a.comments and os.path.isfile(a.comments):
        text = open(a.comments, encoding="utf-8").read()
        listed = ids_in(text)
        missing = [c for c in listed if c not in report["comment_ids_in_change_log"]]
        report["comment_ids_in_source"] = listed
        report["comments_without_change_log_entry"] = missing
        if missing:
            report["problems"].append(
                f"{len(missing)} reviewer comment(s) have no change_log entry: {', '.join(missing)}")

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
