#!/usr/bin/env python3
"""Read the task statement (task.md) into line-anchored, machine-checkable cards.

At runtime the evaluator hands over /input/case.json *and* task.md. The task file
carries things the case often does not repeat: the output requirements table, the
budget, named fonts or materials, and the scoring dimensions. Reading them by hand
each run is where agents drift, so this module turns every markdown table row and
every budget/font statement into a record with a `task.md:L<n>` locator.

Nothing here interprets the science; it only makes the task's own words citable.

CLI:
  python3 taskcard.py extract task.md -o taskcard.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

FONT_RE = re.compile(r"(Liberation\s+(?:Serif|Sans|Mono)|Times New Roman|DejaVu\s+(?:Sans|Serif)(?:\s+Mono)?|"
                     r"Noto\s+(?:Sans|Serif)(?:\s+CJK\s+\w+)?|Source Han\s+\w+|Arial|Helvetica|SimSun|SimHei)")
BUDGET_RE = re.compile(r"(\d+)\s*(model requests|seconds|tokens|次模型请求|秒|files|KB|MB)", re.I)
PATH_RE = re.compile(r"(/(?:input|app|skills)/[\w./-]*)")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*", line))


def extract(path: str) -> dict:
    name = os.path.basename(path)
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    heading, tables, cur = None, [], None
    headings = []
    for i, line in enumerate(lines, 1):
        if re.match(r"^\s*#{1,6}\s", line) or re.match(r"^\s*\*\*[^*]+\*\*\s*$", line):
            heading = line.strip("# *").strip()
            headings.append({"text": heading, "locator": f"{name}:L{i}"})
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            if _is_rule(line):
                continue
            if cur is None:
                cur = {"heading": heading, "header": _cells(line), "locator": f"{name}:L{i}", "rows": []}
                tables.append(cur)
            else:
                cells = _cells(line)
                cur["rows"].append({"cells": dict(zip(cur["header"], cells)) if len(cells) == len(cur["header"])
                                    else {"raw": cells}, "locator": f"{name}:L{i}"})
        else:
            cur = None

    text = "\n".join(lines)
    budgets, fonts, paths = [], [], []
    for i, line in enumerate(lines, 1):
        for m in BUDGET_RE.finditer(line):
            budgets.append({"value": int(m.group(1)), "unit": m.group(2), "locator": f"{name}:L{i}",
                            "quote": line.strip()[:200]})
        for m in FONT_RE.finditer(line):
            fonts.append({"font": m.group(1), "locator": f"{name}:L{i}"})
        for m in PATH_RE.finditer(line):
            paths.append({"path": m.group(1).rstrip("."), "locator": f"{name}:L{i}"})

    def _pick(word):
        return [t for t in tables if t["heading"] and word in t["heading"]] or \
               [t for t in tables if any(word in h for h in t["header"])]

    return {
        "source": name, "line_count": len(lines),
        "headings": headings, "tables": tables,
        "output_requirements": [r for t in tables for r in t["rows"]
                                if any("字段" in h or "field" in h.lower() for h in t["header"])],
        "scoring_dimensions": [r for t in _pick("评分") for r in t["rows"]],
        "deliverables": [r for t in tables for r in t["rows"]
                         if any("材料" in h for h in t["header"]) and any("交付" in h for h in t["header"])],
        "budgets": budgets, "fonts_named": fonts, "runtime_paths": paths,
        "chars": len(text),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Line-anchored cards from task.md")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("task")
    ex.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    if not os.path.isfile(a.task):
        print(json.dumps({"error": "task_missing", "path": a.task}))
        return 2
    payload = extract(a.task)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"out": a.out, "tables": len(payload["tables"]),
                      "output_requirements": len(payload["output_requirements"]),
                      "scoring_dimensions": len(payload["scoring_dimensions"]),
                      "deliverables": len(payload["deliverables"]),
                      "budgets": len(payload["budgets"]), "fonts_named": len(payload["fonts_named"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
