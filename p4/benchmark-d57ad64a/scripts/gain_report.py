#!/usr/bin/env python3
"""Measure the skill's own effect on a case, mechanically.

The scoring rubric asks for a *verifiable* gain over an unassisted agent, not a
claim about one. This computes the quantities that actually differ, on whatever
outputs are at hand:

  * contract compliance        - does result.json satisfy its published contract
  * locator coverage           - share of claims/fields that carry a real locator
  * evidence exactness         - share of quoted evidence that is verbatim present
  * honesty labelling          - share of missing/unreadable items marked as such
                                 instead of silently dropped or filled with a default
  * fabrication risk           - numbers/entities in the output that appear nowhere
                                 in the material actually read

Run it twice - once on a baseline output, once on the skill-assisted output - and
diff. Both numbers come from files, so the comparison is auditable.

CLI:
  python3 gain_report.py report  --result result.json [--blocks blocks.json]
                                 [--material material.txt] [--contract contract.json]
                                 [--family reading] [-o report.json]
  python3 gain_report.py compare baseline_report.json skill_report.json -o gain.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract as contract_mod  # noqa: E402
import evidence as evidence_mod  # noqa: E402

NUMBER_RE = evidence_mod.NUMBER_RE
STRUCTURAL_NUM_RE = re.compile(r"(?i)\b(?:section|chapter|table|figure|fig\.?|part|appendix|step|part)\s+\d+\b")
# "Section 1", "Table 2" and "step 3" are pointers into the deliverable's structure,
# not claims about the material, so they must not be counted as fabricated values.
LOCATOR_NUM_RE = re.compile(r"[\w./\-]+\.[A-Za-z]{2,5}:\d+(?:-\d+)?|\bp\d+b\d+\b")
# A locator embeds a line/block number to say *where* something was read. That number
# is a position, not a datum, so it must not be scored as a fabricated value either.
ABSENT_RE = re.compile(r"\b(?:not[_ ]reported|unreported|missing|unable|unknown|"
                       r"insufficient[_ ]evidence|not[_ ]applicable|n/?a|"
                       r"\u672a\u62a5\u544a|\u65e0\u6cd5\u5224\u65ad|\u7f3a\u5931|\u4e0d\u9002\u7528)\b", re.I)


def _iter_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_strings(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def _collect_locators(obj, out, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("locator", "location", "paragraph_id", "page_id", "source_locator") and isinstance(v, str):
                out.append((f"{prefix}.{k}", v))
            yield from _collect_locators(v, out, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _collect_locators(v, out, f"{prefix}[{i}]")


def report(result_path: str, blocks_path: str | None, material_path: str | None,
           family: str | None, contract_json: str | None) -> dict:
    result = json.load(open(result_path, encoding="utf-8"))
    problems = contract_mod.check(result, family, contract_json)["problems"]

    locators: list[tuple[str, str]] = []
    list(_collect_locators(result, locators))
    loc_found = [x for x in locators if x[1] and not ABSENT_RE.search(x[1]) and x[1].strip() not in ("", "-", "?")]
    locator_coverage = (len(loc_found) / len(locators)) if locators else None

    index = None
    if blocks_path and os.path.isfile(blocks_path):
        index = evidence_mod.LocatorIndex(json.load(open(blocks_path, encoding="utf-8")))

    quotes = [(k, v) for k, v in _iter_strings(result)
              if any(t in k.lower() for t in ("quote", "evidence_text", "excerpt"))]
    exact = None
    if quotes and index is not None:
        checked = [evidence_mod.verify_quote(index, q) for _, q in quotes]
        exact = sum(1 for c in checked if c["exact"]) / len(checked)
    elif quotes:
        exact = None

    # honesty: of every short decision-shaped field, how many state a recognised
    # verdict/absence instead of an ad-hoc word? Long prose fields are excluded so
    # the metric cannot be gamed by verbosity.
    honesty_items, honesty_labelled = 0, 0
    for key, value in _iter_strings(result):
        tail = key.rsplit(".", 1)[-1].lower()
        if not any(t in tail for t in ("status", "verdict", "state", "not_reported", "uncertainty")):
            continue
        if len(value) > 40:
            continue
        honesty_items += 1
        if ABSENT_RE.search(value) or value.strip().lower() in (
                "answered", "ok", "supported", "partial", "partially_supported",
                "contradicted", "insufficient_evidence", "unable", "input_error", "not_reported"):
            honesty_labelled += 1
    honesty = (honesty_labelled / honesty_items) if honesty_items else None

    material_text = ""
    if material_path and os.path.isfile(material_path):
        material_text = evidence_mod.normalize(open(material_path, encoding="utf-8", errors="replace").read())
    elif index is not None:
        material_text = evidence_mod.normalize(" ".join(b.get("text", "") for b in index.blocks))
    unsupported_numbers = []
    if material_text:
        haystack = material_text.replace(",", "")
        for key, value in _iter_strings(result):
            if "quote" in key.lower():
                continue
            if key.split(".")[0].split("[")[0] in ("limitations", "uncertainties", "exceptions"):
                # These fields exist to say what was *not* done ("3 cases x 3 repeats were
                # not run"). Their numbers come from the task, not from the material, so
                # scanning them would penalise the honest labelling they are there to give.
                continue
            for m in NUMBER_RE.finditer(LOCATOR_NUM_RE.sub(" ", STRUCTURAL_NUM_RE.sub(" ", value))):
                token = m.group(0).strip().replace(",", "")
                if not re.search(r"\d", token):
                    continue
                if token not in haystack and token.rstrip(".") not in haystack:
                    unsupported_numbers.append({"field": key, "value": token})
        unsupported_numbers = unsupported_numbers[:50]
    fabrication_risk = (len(unsupported_numbers) /
                        max(1, len([1 for _k, v in _iter_strings(result) if NUMBER_RE.search(v)]))) if material_text else None

    return {
        "result": os.path.abspath(result_path),
        "family": family or contract_mod.guess_family(result),
        "contract_problems": problems,
        "contract_ok": not problems,
        "locators_present": len(loc_found),
        "locators_total": len(locators),
        "locator_coverage": locator_coverage,
        "quotes_checked": len(quotes),
        "evidence_exactness": exact,
        "honesty_labelling": honesty,
        "fabrication_risk": fabrication_risk,
        "unsupported_numbers_sample": unsupported_numbers[:10],
        "notes": [
            "locator_coverage 与 evidence_exactness 需要 --blocks；"
            "fabrication_risk 需要 --material 或 --blocks"
        ],
    }


def compare(base_path: str, skill_path: str) -> dict:
    base = json.load(open(base_path, encoding="utf-8"))
    skill = json.load(open(skill_path, encoding="utf-8"))
    keys = ["contract_ok", "locator_coverage", "evidence_exactness",
            "honesty_labelling", "fabrication_risk"]
    delta = {}
    for key in keys:
        b, s = base.get(key), skill.get(key)
        if isinstance(b, bool) or isinstance(s, bool):
            delta[key] = {"baseline": b, "skill": s, "changed": b != s}
        elif isinstance(b, (int, float)) and isinstance(s, (int, float)):
            delta[key] = {"baseline": b, "skill": s, "delta": round(s - b, 4)}
        else:
            delta[key] = {"baseline": b, "skill": s}
    improved = [k for k, v in delta.items()
                if isinstance(v.get("delta"), (int, float)) and
                ((k == "fabrication_risk" and v["delta"] < 0) or (k != "fabrication_risk" and v["delta"] > 0))]
    return {"baseline": base, "skill": skill, "delta": delta,
            "improved_on": improved,
            "regressed_on": [k for k in delta if k not in improved and isinstance(delta[k].get("delta"), (int, float))
                             and delta[k]["delta"] != 0]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Mechanical gain report for a case result")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--result", required=True)
    r.add_argument("--blocks")
    r.add_argument("--material")
    r.add_argument("--contract")
    r.add_argument("--family")
    r.add_argument("-o", "--out")
    c = sub.add_parser("compare")
    c.add_argument("baseline"); c.add_argument("skill")
    c.add_argument("-o", "--out")
    a = ap.parse_args()
    if a.cmd == "report":
        payload = report(a.result, a.blocks, a.material, a.family, a.contract)
    else:
        payload = compare(a.baseline, a.skill)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(a.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
