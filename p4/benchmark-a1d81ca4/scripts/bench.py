#!/usr/bin/env python3
"""Offline scorer for a literature-reading case set: cases in, per-item scores out.

The benchmark asks one question of a system: for a fixed material and a fixed
list of fields, did it produce the value that is actually written there, at the
place it says it saw it? This scorer answers that mechanically and refuses to
guess. It never awards credit for a plausible-looking value whose locator does
not resolve, and it never turns "the material does not say" into "0".

Check families (each a separately scored item):

  literal       the answer must match the expected string after normalisation
  number        value must match numerically; a swapped/missing unit fails
  enum          the answer must be one of the declared values
  locator       the claimed `p<page>b<block>` must exist AND contain the quote
  not_reported  the material really does not report it; any concrete value is
                scored 0 as fabrication rather than credited as an answer
  order         reading order of the given blocks (LCS ratio against expected)
  table         cell-by-cell agreement against the expected grid

Fault tolerance is part of the contract, not an afterthought: a missing field, a
wrong type, an empty answer or a malformed prediction yields 0 for that item and
is counted, never raised. The summary reports how many such cases occurred, so a
low score caused by a broken prediction file cannot be mistaken for a weak model.

CLI:
  python3 bench.py score --cases cases.jsonl --predictions predictions.jsonl \
      [--blocks blocks.json] -o score.json [--failures failures.jsonl]
  python3 bench.py demo -o score.json [--failures failures.jsonl]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence as evidence_mod  # noqa: E402

FAMILIES = ("literal", "number", "enum", "locator", "not_reported", "order", "table")
ABSENT_WORDS = {"not_reported", "unreported", "unable", "insufficient_evidence",
                "unknown", "n/a", "na", "none", "missing", "不适用", "未报告", "无法判断", "缺失"}
MAX_DETAIL_CHARS = 240


def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_DETAIL_CHARS else text[:MAX_DETAIL_CHARS] + "…"


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append({"__parse_error__": f"line {lineno}: {exc}", "case_id": f"line{lineno}"})
    return rows


def _norm(text) -> str:
    return evidence_mod.normalize(text if isinstance(text, str) else str(text)).lower()


def _plain(text) -> str:
    return " ".join(str(text).split())


def _num(value):
    """Parse the leading number out of a value; None when there is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    if not isinstance(value, str):
        return None
    m = evidence_mod.NUMBER_RE.search(value)
    if not m:
        return None
    token = m.group(0).replace(",", "").strip()
    token = token.replace("−", "-")
    try:
        return float(token.split()[0].rstrip("%‰"))
    except ValueError:
        return None


def _unit(value) -> str:
    if not isinstance(value, str):
        return ""
    m = evidence_mod.NUMBER_RE.search(value)
    if not m:
        return ""
    return " ".join(m.group(0).replace(",", "").split()[1:]).lower()


def _is_absent(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    text = str(value).strip().lower().strip("_ ")
    return text in ABSENT_WORDS or text == ""


# --------------------------------------------------------------------------- #
# check families
# --------------------------------------------------------------------------- #
def check_literal(expected, actual, ctx) -> tuple[float, dict]:
    if _is_absent(actual):
        return 0.0, {"reason": "empty answer", "expected": _short(expected)}
    if _plain(expected) == _plain(actual):
        return 1.0, {"reason": "exact"}
    if _norm(expected) == _norm(actual):
        return 0.9, {"reason": "matches after normalisation (whitespace/ligature/case)",
                     "expected": _short(expected), "actual": _short(actual)}
    return 0.0, {"reason": "text differs", "expected": _short(expected), "actual": _short(actual)}


def check_number(expected, actual, ctx) -> tuple[float, dict]:
    want = _num(expected)
    got = _num(actual)
    if got is None:
        return 0.0, {"reason": "no number in the answer", "expected": _short(expected),
                     "actual": _short(actual)}
    if want is None:
        return 0.0, {"reason": "expected value is not numeric; case is malformed",
                     "expected": _short(expected)}
    tol = ctx.get("tolerance", 1e-6)
    if not math.isclose(want, got, rel_tol=tol, abs_tol=tol):
        return 0.0, {"reason": "numeric value differs", "expected": want, "actual": got}
    want_unit, got_unit = _unit(expected), _unit(actual)
    if want_unit and got_unit and want_unit != got_unit:
        return 0.5, {"reason": "value right, unit differs", "expected_unit": want_unit,
                     "actual_unit": got_unit}
    if want_unit and not got_unit:
        return 0.75, {"reason": "value right, unit omitted (do not impute one)", "unit": want_unit}
    return 1.0, {"reason": "exact numeric match", "value": got}


def check_enum(expected, actual, ctx) -> tuple[float, dict]:
    allowed = ctx.get("values") or ([expected] if isinstance(expected, str) else list(expected or []))
    if not isinstance(actual, str):
        return 0.0, {"reason": f"answer is {type(actual).__name__}, expected one of {allowed}",
                     "actual": _short(actual)}
    if actual.strip() in allowed:
        return 1.0, {"reason": "in the declared enum"}
    return 0.0, {"reason": "not one of the declared values", "expected": allowed,
                 "actual": _short(actual)}


def check_locator(expected, actual, ctx) -> tuple[float, dict]:
    """The claimed locator must resolve, and the block it names must hold the quote."""
    index = ctx.get("index")
    locator = actual if isinstance(actual, str) else (actual or {}).get("locator")
    quote = ctx.get("quote") or (actual or {}).get("quote") if isinstance(actual, dict) else ctx.get("quote")
    quote = quote or expected
    if not isinstance(locator, str) or not locator.strip():
        return 0.0, {"reason": "no locator supplied", "quote": _short(quote)}
    if index is None:
        return 0.0, {"reason": "no blocks file supplied; a locator cannot be verified"}
    block = index.by_locator.get(locator.strip())
    if block is None:
        return 0.0, {"reason": f"locator {locator!r} does not exist in the material read"}
    if not quote or _is_absent(quote):
        return 0.5, {"reason": "locator resolves but no quote was given to check it against",
                     "locator": locator}
    hit = _norm(quote) in _norm(block)
    if hit:
        return 1.0, {"reason": "locator resolves and holds the quote", "locator": locator}
    found = index.locate(quote, limit=1)
    return 0.0, {"reason": "locator resolves but the quote is elsewhere",
                 "locator": locator, "quote_actually_at": found[0]["locator"] if found else None,
                 "quote": _short(quote)}


def check_not_reported(expected, actual, ctx) -> tuple[float, dict]:
    """Rewards honesty: the field is genuinely absent, so any value is a fabrication."""
    if isinstance(actual, dict):
        status = actual.get("status")
        if isinstance(status, str) and status.strip().lower() in ABSENT_WORDS:
            return 1.0, {"reason": "correctly marked as not reported"}
        value = actual.get("value")
        if _is_absent(value) and _is_absent(status):
            return 1.0, {"reason": "correctly left empty"}
        return 0.0, {"reason": "material does not report this field but a value was supplied",
                     "actual": _short(value if value is not None else status)}
    if _is_absent(actual):
        return 1.0, {"reason": "correctly marked as not reported"}
    return 0.0, {"reason": "material does not report this field but a value was supplied",
                 "actual": _short(actual)}


def check_order(expected, actual, ctx) -> tuple[float, dict]:
    want = [str(x) for x in (expected or [])]
    got = [str(x) for x in (actual or [])] if isinstance(actual, list) else []
    if not want:
        return 0.0, {"reason": "case has no expected order"}
    if not got:
        return 0.0, {"reason": "empty or non-list answer", "actual": _short(actual)}
    # LCS ratio: forgiving about extras, strict about inversions that change meaning
    prev = [0] * (len(got) + 1)
    for a in want:
        cur = [0] * (len(got) + 1)
        for j, b in enumerate(got, start=1):
            cur[j] = prev[j - 1] + 1 if a == b else max(prev[j], cur[j - 1])
        prev = cur
    ratio = prev[-1] / len(want)
    return round(ratio, 4), {"reason": f"{prev[-1]}/{len(want)} expected blocks in order",
                             "expected": want[:12], "actual": got[:12]}


def check_table(expected, actual, ctx) -> tuple[float, dict]:
    if not isinstance(actual, list) or not actual:
        return 0.0, {"reason": "empty or non-list table", "actual": _short(actual)}
    want_rows = [r for r in (expected or []) if isinstance(r, list)]
    if not want_rows:
        return 0.0, {"reason": "case has no expected grid"}
    total = correct = 0
    diffs = []
    for ri, want_row in enumerate(want_rows):
        got_row = actual[ri] if ri < len(actual) and isinstance(actual[ri], list) else []
        for ci, cell in enumerate(want_row):
            total += 1
            got = got_row[ci] if ci < len(got_row) else None
            if _plain(cell) == _plain(got):
                correct += 1
            elif len(diffs) < 8:
                diffs.append({"cell": f"r{ri}c{ci}", "expected": _short(cell), "actual": _short(got)})
    if not total:
        return 0.0, {"reason": "expected grid had no cells"}
    return round(correct / total, 4), {"reason": f"{correct}/{total} cells match", "diffs": diffs}


CHECKERS = {
    "literal": check_literal,
    "number": check_number,
    "enum": check_enum,
    "locator": check_locator,
    "not_reported": check_not_reported,
    "order": check_order,
    "table": check_table,
}


# --------------------------------------------------------------------------- #
# prediction access
# --------------------------------------------------------------------------- #
def field_of(prediction: dict, field_id: str):
    """Find a field in either a flat `fields` map or a reading-family `result` dict."""
    if not isinstance(prediction, dict):
        return None, False
    fields = prediction.get("fields")
    if isinstance(fields, dict) and field_id in fields:
        return fields[field_id], True
    result = prediction.get("result")
    if isinstance(result, dict) and field_id in result:
        return result[field_id], True
    return None, False


def unwrap(actual, family: str):
    """An item may be a bare value or {value|status, locator, quote}."""
    if isinstance(actual, dict) and family in ("literal", "number", "enum", "not_reported"):
        if "value" in actual:
            return actual["value"]
        if "status" in actual:
            return actual["status"]
    return actual


def score(cases: list[dict], predictions: list[dict], index) -> dict:
    by_case = {}
    for p in predictions:
        if isinstance(p, dict) and isinstance(p.get("case_id"), str):
            by_case[p["case_id"]] = p

    items = []
    failures = []
    exception_count = 0
    parse_errors = [p.get("__parse_error__") for p in predictions if isinstance(p, dict) and "__parse_error__" in p]
    seen_cases = set()

    for case in cases:
        case_id = case.get("case_id")
        field_id = case.get("field_id")
        family = case.get("kind") or case.get("family")
        expected = case.get("expected")
        seen_cases.add(case_id)

        if not family or family not in CHECKERS:
            items.append({"case_id": case_id, "field_id": field_id, "kind": family,
                          "score": 0.0, "applicable": False,
                          "reason": f"unknown check family {family!r}; add it to CHECKERS"})
            continue

        prediction = by_case.get(case_id)
        if prediction is None:
            items.append({"case_id": case_id, "field_id": field_id, "kind": family,
                          "score": 0.0, "applicable": True,
                          "reason": "no prediction for this case"})
            failures.append({"case_id": case_id, "field_id": field_id, "kind": family,
                             "reason": "no prediction for this case",
                             "expected": _short(expected)})
            continue

        actual, present = field_of(prediction, field_id)
        ctx = {
            "index": index,
            "tolerance": case.get("tolerance", 1e-6),
            "values": case.get("values"),
            "quote": case.get("quote"),
        }
        try:
            if not present:
                value, detail = (0.0, {"reason": f"field {field_id!r} missing from the prediction"}) \
                    if family != "not_reported" else (1.0, {"reason": "field absent from prediction; "
                                                                     "the material does not report it"})
                # a missing field is only creditable under not_reported when the prediction
                # explicitly carries an empty/null entry; a silent omission is a dropped field.
                if family == "not_reported":
                    value, detail = 0.0, {"reason": f"field {field_id!r} was dropped, not marked absent"}
            else:
                value, detail = CHECKERS[family](expected, unwrap(actual, family), ctx)
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the run
            exception_count += 1
            value, detail = 0.0, {"reason": f"{type(exc).__name__}: {exc}"}

        item = {"case_id": case_id, "field_id": field_id, "kind": family,
                "score": round(float(value), 4), "applicable": True,
                "reason": detail.get("reason"), "detail": detail}
        items.append(item)
        if value < 1.0:
            failures.append({"case_id": case_id, "field_id": field_id, "kind": family,
                             "score": item["score"], "reason": detail.get("reason"),
                             "expected": _short(expected), "actual": _short(actual),
                             "detail": detail})

    per_case = {}
    for item in items:
        if not item["applicable"]:
            continue
        bucket = per_case.setdefault(item["case_id"], {"score_sum": 0.0, "items": 0, "failed": 0})
        bucket["score_sum"] += item["score"]
        bucket["items"] += 1
        bucket["failed"] += 1 if item["score"] < 1.0 else 0
    for case_id, bucket in per_case.items():
        bucket["mean"] = round(bucket["score_sum"] / bucket["items"], 4) if bucket["items"] else None
        bucket["passed"] = bucket["failed"] == 0 and bucket["items"] > 0

    scored = [i for i in items if i["applicable"]]
    score_sum = sum(i["score"] for i in scored)
    return {
        "summary": {
            "cases": len(per_case),
            "items": len(scored),
            # max_score is the ceiling (one point per applicable item), not the sum of the
            # achieved scores - reporting the latter made a 8/12 run print "max_score: 8.0".
            "max_score": float(len(scored)),
            "score_sum": round(score_sum, 4),
            "mean_score": round(score_sum / len(scored), 4) if scored else None,
            "fully_correct_items": sum(1 for i in scored if i["score"] >= 1.0),
            "failed_items": sum(1 for i in scored if i["score"] < 1.0),
            "skipped_items": len(items) - len(scored),
            "exception_count": exception_count,
            "prediction_parse_errors": len(parse_errors),
            "cases_without_prediction": sorted(c for c in seen_cases if c not in by_case),
            "families": sorted({i["kind"] for i in items if i["kind"]}),
        },
        "per_case": per_case,
        "per_item": items,
        "failures": failures,
    }


# --------------------------------------------------------------------------- #
# demo case set (synthetic, clearly labelled - never a real result)
# --------------------------------------------------------------------------- #
DEMO_CASES = [
    {"case_id": "DEMO", "field_id": "sample_size", "kind": "number", "expected": "n = 32"},
    {"case_id": "DEMO", "field_id": "dose", "kind": "number", "expected": "42.5 mg/L"},
    {"case_id": "DEMO", "field_id": "design", "kind": "literal", "expected": "randomised controlled trial"},
    {"case_id": "DEMO", "field_id": "verdict", "kind": "enum",
     "expected": "supported", "values": ["supported", "contradicted", "insufficient_evidence"]},
    {"case_id": "DEMO", "field_id": "quote_binding", "kind": "locator",
     "expected": "reduced biomass by 31% relative to control",
     "quote": "reduced biomass by 31% relative to control"},
    {"case_id": "DEMO", "field_id": "funding", "kind": "not_reported", "expected": None},
]

# one good prediction set and one deliberately broken set, so the scorer's own
# fault tolerance is demonstrated rather than asserted
DEMO_GOOD = {"case_id": "DEMO", "fields": {
    "sample_size": {"value": "32 participants"},
    "dose": {"value": "42.5 mg/L"},
    "design": {"value": "randomised controlled trial"},
    "verdict": {"value": "supported"},
    "quote_binding": {"locator": "p3b7", "quote": "reduced biomass by 31% relative to control"},
    "funding": {"status": "not_reported"},
}}

DEMO_BAD = {"case_id": "DEMO", "fields": {
    "sample_size": {"value": "约三十人"},
    "dose": {"value": "42.5 ug/mL"},
    "design": {"value": "cohort study"},
    "verdict": "supported",                       # bare string, not an object
    "quote_binding": {"locator": "p99b99", "quote": "reduced biomass by 31% relative to control"},
    "funding": {"value": "NIH grant R01-123456"},  # invented
}}


def build_demo_index() -> "evidence_mod.LocatorIndex":
    blocks = {"blocks": [
        {"id": "p3b6", "page": 3, "type": "body", "text": "Control cultures were left untreated."},
        {"id": "p3b7", "page": 3, "type": "body",
         "text": "Exposure to the compound reduced biomass by 31% relative to control (Figure 2)."},
        {"id": "p3b8", "page": 3, "type": "body", "text": "No funding statement appears in this document."},
    ]}
    return evidence_mod.LocatorIndex(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline scorer for a literature-reading case set")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--cases", required=True)
    sc.add_argument("--predictions", required=True)
    sc.add_argument("--blocks")
    sc.add_argument("-o", "--out", required=True)
    sc.add_argument("--failures")
    dm = sub.add_parser("demo")
    dm.add_argument("-o", "--out", required=True)
    dm.add_argument("--failures")
    a = ap.parse_args()

    if a.cmd == "demo":
        index = build_demo_index()
        payload = score(DEMO_CASES, [DEMO_GOOD], index)
        payload["demo"] = True
        payload["note"] = ("Synthetic demonstration of the scorer's behaviour, including a bad "
                           "prediction set; not a benchmark result for any real system.")
        bad = score(DEMO_CASES, [DEMO_BAD], index)
        payload["demo_bad_prediction"] = bad["summary"]
        failures = payload["failures"] + [dict(f, prediction="bad") for f in bad["failures"]]
    else:
        index = None
        if a.blocks and os.path.isfile(a.blocks):
            index = evidence_mod.LocatorIndex(json.load(open(a.blocks, encoding="utf-8")))
        cases = load_jsonl(a.cases)
        predictions = load_jsonl(a.predictions)
        payload = score(cases, predictions, index)
        failures = payload["failures"]

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    if a.failures:
        with open(a.failures, "w", encoding="utf-8") as fh:
            for row in failures:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
