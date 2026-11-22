#!/usr/bin/env python3
"""Mechanical scorer for academic-writing cases.

The skill's claim is a closed loop: case spec -> reference-answer tolerance ->
observable anchors -> per-item score -> report. This script is the part that
makes the loop executable. It never judges prose quality; it decides only what
is mechanically decidable (a number survived, a citation is registered, a
section exists, a locator resolves, a hedge was not upgraded) and refuses to
score what it cannot see.

Subcommands
  spec      case.json -> spec.json      derive checkable items from the contract
  selfcheck spec.json                   is the spec scorable at all?
  score     spec.json result.json ...   -> score.json + failures.jsonl
  report    score.json -> report.md     per-item score, evidence, failure, advice
  calibrate A.json B.json               two scorers -> agreement + adjudication list

Exit codes: 0 all scored items pass; 1 something failed or was unscorable;
2 usage/IO error. A single bad item never aborts a batch: it is recorded as a
`check_error` failure with score 0 and counted, per the skill's fault rules.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence as evidence_mod  # noqa: E402

NUMBER_RE = evidence_mod.NUMBER_RE
CITE_RE = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]|\\cite[tp]?\{([^}]+)\}|\(([A-Z][A-Za-z\-]+"
                     r"(?: et al\.?)?,\s*\d{4}[a-z]?)\)")
HEDGE_RE = re.compile(r"\b(?:may|might|could|suggests?|suggesting|indicates?|appears? to|"
                      r"possibly|potentially|tends? to|is likely|we propose|"
                      r"可能|或许|提示|倾向于)\b", re.I)
HYPE_RE = re.compile(r"\b(?:novel|unprecedented|breakthrough|revolutionary|first-time|"
                     r"definitive|proves? that|clearly demonstrates?|"
                     r"首次|突破性|前所未有|毫无疑问地证明)\b", re.I)
PLACEHOLDER_RE = re.compile(r"(TODO|FIXME|PLACEHOLDER|TBD|XXX|待补充|待填写|\?\?\?)")
ABSENT_RE = re.compile(r"(not_reported|unreported|unable|insufficient_evidence|"
                       r"未报告|无法判断|材料缺失|缺失)")

FAILURE_TYPES = (
    "contract_field_missing", "case_id_mismatch", "number_dropped",
    "number_unverifiable", "citation_unregistered", "citation_uncited",
    "section_missing", "term_inconsistent", "modality_upgrade", "hype_language",
    "locator_unresolvable", "quote_not_verbatim", "placeholder_left",
    "status_without_limits", "check_error",
)

DEFAULT_BANDS = [[0.90, "A"], [0.75, "B"], [0.60, "C"], [0.0, "D"]]


# ---------------------------------------------------------------- utilities

def _as_text(value) -> str:
    """Flatten a contract field to searchable text without inventing content."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _norm(text: str) -> str:
    return evidence_mod.normalize(text or "")


HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*.*$")
SECTION_WORD_RE = re.compile(r"(?i)\b(?:section|chapter|table|figure|fig\.?|part|appendix)\s+\d+\b")


def _numbers(text: str) -> list[str]:
    """Numeric tokens carrying content, not structural numbering.

    A heading number ("Section 1", "## 2.3 Methods", "1. Introduction") is not a
    claim about the material, so it must not be scored as an invented number.
    """
    body = HEADING_RE.sub(" ", text or "")
    body = SECTION_WORD_RE.sub(" ", body)
    body = re.sub(r"(?m)^\s*\d+(?:\.\d+)*[.)]?\s+(?=\S)", " ", body)
    return [m.group(0).strip() for m in NUMBER_RE.finditer(body)]


def _cite_numbers(text: str) -> list[int]:
    nums: list[int] = []
    for m in CITE_RE.finditer(text):
        if not m.group(1):
            continue
        for part in re.split(r"\s*[,，]\s*", m.group(1)):
            if re.search(r"[-–]", part):
                lo_hi = [int(x) for x in re.split(r"\s*[-–]\s*", part)[:2] if x.isdigit()]
                if len(lo_hi) == 2 and lo_hi[0] <= lo_hi[1] and lo_hi[1] - lo_hi[0] < 60:
                    nums.extend(range(lo_hi[0], lo_hi[1] + 1))
            elif part.strip().isdigit():
                nums.append(int(part))
    return nums


def band_of(ratio: float, bands) -> str:
    for threshold, label in bands:
        if ratio >= threshold:
            return label
    return bands[-1][1]


def _check(cid, name, kind, max_score, status, score, failure_type=None,
           evidence=None, suggestion=""):
    return {"id": cid, "name": name, "kind": kind, "max": max_score,
            "status": status, "score": round(float(score), 3),
            "failure_type": failure_type, "evidence": evidence or [],
            "suggestion": suggestion}


# ---------------------------------------------------------------- spec

CONTRACT_KINDS = {
    "case_id": "the case id copied verbatim",
    "deliverable": "an editable artifact path + format that exists on disk",
    "preserved_facts": "every number/condition/term with original value, locator, changed flag",
    "evidence_map": "each key claim bound to a locator and a verbatim quote",
    "change_log": "each edit: action, reason, human_check, unresolved items",
    "status": "one of answered|partial|insufficient_evidence|input_error",
    "limitations": "what is missing and where a human takes over",
    "uncertainties": "open questions, marked as such",
    "claims": "atomic claims with evidence",
    "result": "the main result object",
}


def cmd_spec(args) -> int:
    case = json.load(open(args.case, encoding="utf-8"))
    contract = case.get("output_contract") or {}
    if not isinstance(contract, dict) or not contract:
        print(json.dumps({"error": "case has no output_contract object"}, ensure_ascii=False))
        return 2

    required = list(contract.keys())
    if "case_id" not in required:
        required.insert(0, "case_id")

    checks = []
    # Contract-shape items: one per published field. Weight 2 so a missing
    # field is visibly worse than a stylistic miss.
    for i, field in enumerate(required, start=1):
        checks.append({
            "id": f"C{i}", "name": f"契约字段 {field}", "kind": "contract_field",
            "field": field, "max": 2,
            "rule": "field present and non-empty, in the published shape",
            "anchor": {"observable": f"result.json has a non-empty '{field}' key",
                       "pass": "key present and carries substance",
                       "fail": "key absent, empty, or a placeholder"},
            "why": CONTRACT_KINDS.get(field, contract.get(field, "")),
        })

    universal = [
        {"id": "N1", "name": "数字保留", "kind": "numbers", "max": 4,
         "rule": "every numeric token in the material appears in the deliverable "
                 "with the same value; no number appears that the material lacks",
         "anchor": {"observable": "set difference of numeric tokens, material vs deliverable",
                    "pass": "0 dropped and 0 unverifiable",
                    "fail": "any dropped or invented number is listed with its locator"}},
        {"id": "R1", "name": "引用双向一致", "kind": "citations", "max": 3,
         "rule": "every marker in the body is in the reference list and vice versa",
         "anchor": {"observable": "set difference of marker numbers, body vs list",
                    "pass": "both differences empty",
                    "fail": "ids listed under cited_but_not_listed / listed_but_not_cited"}},
        {"id": "S1", "name": "结构齐全", "kind": "sections", "max": 3,
         "rule": "every section named by the case instruction is present",
         "anchor": {"observable": "heading match for each required section",
                    "pass": "all headings present",
                    "fail": "missing headings named individually"}},
        {"id": "T1", "name": "术语一致", "kind": "glossary", "max": 2,
         "rule": "a term and its approved renderings are used consistently",
         "anchor": {"observable": "forbidden variants present in the deliverable",
                    "pass": "no forbidden variant",
                    "fail": "variant + the approved rendering listed"}},
        {"id": "M1", "name": "模态未升级", "kind": "modality", "max": 3,
         "rule": "a hedged source claim is not restated as certain",
         "anchor": {"observable": "hedge in material quote vs hedge in mapped claim",
                    "pass": "hedged source stays hedged",
                    "fail": "claim drops may/suggests while its quote keeps it"}},
        {"id": "H1", "name": "无夸大词", "kind": "hype", "max": 1,
         "rule": "no unsupported novelty/superlative wording",
         "anchor": {"observable": "banned-term hits in deliverable",
                    "pass": "0 hits", "fail": "each hit with its 60-char context"}},
        {"id": "E1", "name": "定位符可解析", "kind": "locator", "max": 3,
         "rule": "every evidence_map locator exists in the material index",
         "anchor": {"observable": "locator string membership in the block index",
                    "pass": "every locator resolves",
                    "fail": "unresolved locators listed verbatim"}},
        {"id": "Q1", "name": "引语逐字可核", "kind": "quote", "max": 4,
         "rule": "every quoted string is present verbatim in the material read",
         "anchor": {"observable": "whitespace/ligature-normalised substring test",
                    "pass": "every quote exact", "fail": "non-exact quotes listed"}},
        {"id": "P1", "name": "无占位残留", "kind": "placeholder", "max": 1,
         "rule": "no TODO/TBD/待补充 marker in any field",
         "anchor": {"observable": "regex hit across the whole result",
                    "pass": "0 hits", "fail": "field + matched token"}},
        {"id": "L1", "name": "非完成态须声明缺口", "kind": "honesty", "max": 2,
         "rule": "status != answered requires limitations/uncertainties",
         "anchor": {"observable": "status value vs non-empty limitations",
                    "pass": "declared, or status=answered", "fail": "silent partial"}},
    ]
    checks.extend(universal)

    spec = {
        "spec_version": "1.0",
        "case_id": case.get("case_id"),
        "case_source": case.get("source"),
        "task_instruction": case.get("task_instruction"),
        "output_contract": contract,
        "required_fields": required,
        "required_sections": case.get("required_sections") or [],
        "glossary": case.get("glossary") or {},
        "material": case.get("material") or [],
        "checks": checks,
        "bands": case.get("bands") or DEFAULT_BANDS,
        "notes": [
            "checks are derived from the published output_contract plus the "
            "universal writing checks N1/R1/S1/T1/M1/H1/E1/Q1/P1/L1",
            "a check with no material to test against is skipped, not passed",
        ],
    }
    out = args.out or "-"
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    if out == "-":
        print(text)
    else:
        open(out, "w", encoding="utf-8").write(text)
        print(out)
    return 0


def cmd_selfcheck(args) -> int:
    spec = json.load(open(args.spec, encoding="utf-8"))
    problems, ids = [], set()
    for c in spec.get("checks", []):
        cid = c.get("id")
        if cid in ids:
            problems.append(f"duplicate check id {cid!r}")
        ids.add(cid)
        if not c.get("rule"):
            problems.append(f"{cid}: no rule")
        anchor = c.get("anchor") or {}
        if not anchor.get("observable"):
            problems.append(f"{cid}: anchor has no observable phenomenon")
        if not anchor.get("pass") or not anchor.get("fail"):
            problems.append(f"{cid}: anchor does not separate pass from fail")
        if not isinstance(c.get("max"), (int, float)) or c.get("max") <= 0:
            problems.append(f"{cid}: weight must be a positive number")
    bands = spec.get("bands") or []
    thresholds = [b[0] for b in bands]
    if thresholds != sorted(thresholds, reverse=True):
        problems.append("bands are not monotone descending")
    if not bands:
        problems.append("no scoring bands: a score cannot be turned into a verdict")
    report = {"spec": args.spec, "checks": len(spec.get("checks", [])),
              "total_weight": sum(c.get("max", 0) for c in spec.get("checks", [])),
              "problems": problems, "ok": not problems}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


# ---------------------------------------------------------------- scoring

def _load_material(args):
    """Return (index_or_None, plain_text, locator_set)."""
    index, text, locators = None, "", set()
    if args.blocks and os.path.isfile(args.blocks):
        payload = json.load(open(args.blocks, encoding="utf-8"))
        index = evidence_mod.LocatorIndex(payload)
        blocks = payload.get("blocks") if isinstance(payload, dict) else payload
        text = "\n".join(b.get("text", "") for b in blocks or [])
        for i, b in enumerate(blocks or []):
            loc = b.get("locator") or (f"p{b.get('page')}b{b.get('id')}"
                                       if b.get("page") is not None and b.get("id") is not None
                                       else f"block{i}")
            locators.add(loc)
    elif args.material and os.path.isfile(args.material):
        text = open(args.material, encoding="utf-8", errors="replace").read()
        for n, line in enumerate(text.splitlines(), start=1):
            locators.add(f"{os.path.basename(args.material)}:{n}")
    return index, text, locators


def _deliverable_text(result: dict, args) -> str:
    path = None
    deliverable = result.get("deliverable")
    if isinstance(deliverable, dict):
        path = deliverable.get("path")
    elif isinstance(deliverable, str):
        path = deliverable
    if args.deliverable:
        path = args.deliverable
    if path and os.path.isfile(path):
        return open(path, encoding="utf-8", errors="replace").read()
    # Fall back to the structured field so structure is still checkable.
    return _as_text(deliverable)


def run_check(check, result, material_text, deliverable_text, index, locators) -> dict:
    kind = check["kind"]
    cid, name, mx = check["id"], check["name"], check["max"]

    if kind == "contract_field":
        field = check["field"]
        value = result.get(field)
        empty = value in (None, "", [], {})
        if empty:
            return _check(cid, name, kind, mx, "fail", 0, "contract_field_missing",
                          [{"field": field, "found": type(value).__name__}],
                          f"填写 {field}：{check.get('why', '')}")
        return _check(cid, name, kind, mx, "pass", mx,
                      evidence=[{"field": field, "shape": type(value).__name__}])

    if kind == "numbers":
        if not material_text:
            return _check(cid, name, kind, mx, "skip", 0, evidence=[{"reason": "no material index"}])
        src = {_norm(n) for n in _numbers(material_text)}
        dst = {_norm(n) for n in _numbers(deliverable_text)}
        if not src:
            return _check(cid, name, kind, mx, "skip", 0,
                          evidence=[{"reason": "material carries no numeric token"}])
        dropped = sorted(src - dst)
        invented = sorted(dst - src)
        # union, not src: an invented number is a defect even when nothing was dropped,
        # so a failing item must never land on the full mark.
        ratio = len(src & dst) / len(src | dst) if (src | dst) else 1.0
        if not dropped and not invented:
            return _check(cid, name, kind, mx, "pass", mx,
                          evidence=[{"numbers_in_material": len(src), "all_present": True}])
        ftype = "number_dropped" if dropped else "number_unverifiable"
        return _check(cid, name, kind, mx, "fail", mx * ratio, ftype,
                      [{"dropped": dropped[:20]}, {"not_in_material": invented[:20]}],
                      "把材料里的数字原样带入；材料没有的数字不要写")

    if kind == "citations":
        text = deliverable_text
        m = list(re.finditer(r"(?im)^#{1,6}\s*(references|参考文献|bibliography)\s*$", text))
        body, refs = (text[:m[-1].start()], text[m[-1].end():]) if m else (text, "")
        listed = {int(mm.group(1)) for mm in re.finditer(r"(?m)^\s*\[(\d+)\]", refs)}
        cited = set(_cite_numbers(body))
        if not cited and not listed:
            return _check(cid, name, kind, mx, "skip", 0,
                          evidence=[{"reason": "no citation markers in deliverable"}])
        unlisted = sorted(cited - listed)
        uncited = sorted(listed - cited)
        ok = not unlisted and not uncited
        return _check(cid, name, kind, mx, "pass" if ok else "fail",
                      mx if ok else mx * (len(cited & listed) / max(1, len(cited | listed))),
                      None if ok else ("citation_unregistered" if unlisted else "citation_uncited"),
                      [{"cited_but_not_listed": unlisted}, {"listed_but_not_cited": uncited}],
                      "补齐参考文献条目，或删除正文里没有对应条目的编号")

    if kind == "sections":
        required = check.get("sections") or []
        if not required:
            return _check(cid, name, kind, mx, "skip", 0,
                          evidence=[{"reason": "case names no required section"}])
        present = [s for s in required
                   if re.search(rf"(?im)^\s*#{{0,6}}\s*{re.escape(s)}\b", deliverable_text)]
        ok = len(present) == len(required)
        return _check(cid, name, kind, mx, "pass" if ok else "fail",
                      mx * len(present) / len(required), None if ok else "section_missing",
                      [{"present": present, "missing": [s for s in required if s not in present]}],
                      "按题面补齐缺失小节")

    if kind == "glossary":
        glossary = check.get("glossary") or {}
        if not glossary:
            return _check(cid, name, kind, mx, "skip", 0, evidence=[{"reason": "no glossary"}])
        bad = []
        for term, allowed in glossary.items():
            allowed = [allowed] if isinstance(allowed, str) else list(allowed)
            for variant in allowed:
                if variant and variant != term and variant in deliverable_text:
                    bad.append({"term": term, "forbidden_variant": variant,
                                "approved": allowed})
        ok = not bad
        return _check(cid, name, kind, mx, "pass" if ok else "fail", mx if ok else 0,
                      None if ok else "term_inconsistent", bad,
                      "统一术语，按 glossary 的批准写法")

    if kind == "modality":
        mapping = result.get("evidence_map") or []
        flags = []
        for i, item in enumerate(mapping):
            if not isinstance(item, dict):
                continue
            quote, claim = item.get("quote") or "", item.get("claim") or ""
            if quote and HEDGE_RE.search(quote) and claim and not HEDGE_RE.search(claim):
                flags.append({"evidence_map": i, "claim": claim[:120],
                              "quote_hedged": True, "claim_hedged": False})
        ok = not flags
        return _check(cid, name, kind, mx, "pass" if ok else "fail", mx if ok else 0,
                      None if ok else "modality_upgrade", flags[:10],
                      "来源是可能/提示，产出就必须保持同样强度")

    if kind == "hype":
        hits = [{"term": m.group(0), "context": deliverable_text[max(0, m.start() - 60):m.end() + 60]}
                for m in HYPE_RE.finditer(deliverable_text)]
        ok = not hits
        return _check(cid, name, kind, mx, "pass" if ok else "fail", mx if ok else 0,
                      None if ok else "hype_language", hits[:10],
                      "删除无材料支撑的夸大措辞")

    if kind == "locator":
        mapping = result.get("evidence_map") or []
        if not mapping:
            return _check(cid, name, kind, mx, "fail", 0, "contract_field_missing",
                          [{"reason": "evidence_map empty"}], "为每条关键论断补 locator")
        if not locators:
            return _check(cid, name, kind, mx, "skip", 0,
                          evidence=[{"reason": "no material index supplied (--blocks/--material)"}])
        unresolved = [{"index": i, "locator": (it.get("locator") if isinstance(it, dict) else None)}
                      for i, it in enumerate(mapping)
                      if not isinstance(it, dict) or it.get("locator") not in locators]
        ok = not unresolved
        return _check(cid, name, kind, mx, "pass" if ok else "fail",
                      mx * (1 - len(unresolved) / max(1, len(mapping))),
                      None if ok else "locator_unresolvable", unresolved[:10],
                      "定位符必须来自真实读到的块（pdf_text.py 的 p<页>b<块>）")

    if kind == "quote":
        mapping = result.get("evidence_map") or []
        quotes = [it.get("quote") for it in mapping if isinstance(it, dict) and it.get("quote")]
        if not quotes:
            return _check(cid, name, kind, mx, "fail", 0, "contract_field_missing",
                          [{"reason": "no quote in evidence_map"}], "为每条论断附逐字引语")
        if index is None:
            return _check(cid, name, kind, mx, "skip", 0,
                          evidence=[{"reason": "no block index; quote check needs --blocks"}])
        checked = [evidence_mod.verify_quote(index, q) for q in quotes]
        bad = [{"quote": c["quote"][:120], "found": c["found"], "exact": c["exact"]}
               for c in checked if not c["exact"]]
        ok = not bad
        return _check(cid, name, kind, mx, "pass" if ok else "fail",
                      mx * (len(checked) - len(bad)) / len(checked),
                      None if ok else "quote_not_verbatim", bad[:10],
                      "引语必须能在材料里逐字命中；找不到就降级为未报告")

    if kind == "placeholder":
        blob = json.dumps(result, ensure_ascii=False) + deliverable_text
        hits = sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(blob)})
        ok = not hits
        return _check(cid, name, kind, mx, "pass" if ok else "fail", mx if ok else 0,
                      None if ok else "placeholder_left", [{"tokens": hits}],
                      "占位符换成真实内容，或写明未报告")

    if kind == "honesty":
        status = result.get("status")
        limits = result.get("limitations") or result.get("uncertainties")
        if status in (None, "answered", "ok"):
            return _check(cid, name, kind, mx, "pass", mx,
                          evidence=[{"status": status, "limits_present": bool(limits)}])
        if limits:
            return _check(cid, name, kind, mx, "pass", mx,
                          evidence=[{"status": status, "limits_present": True}])
        return _check(cid, name, kind, mx, "fail", 0, "status_without_limits",
                      [{"status": status}], "非完成态必须写清缺什么、谁接手")

    return _check(cid, name, kind, mx, "skip", 0, evidence=[{"reason": f"unknown kind {kind}"}])


def score_spec(spec, result, material_text, deliverable_text, index, locators) -> dict:
    items, failures = [], []
    for check in spec.get("checks", []):
        try:
            item = run_check(check, result, material_text, deliverable_text, index, locators)
        except Exception as exc:  # one bad item must not kill the batch
            item = _check(check.get("id", "?"), check.get("name", "?"),
                          check.get("kind", "?"), check.get("max", 0),
                          "error", 0, "check_error",
                          [{"error": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc(limit=2)}],
                          "该项未能执行，计 0 并计入 error_count")
        items.append(item)
        if item["status"] in ("fail", "error"):
            failures.append({"check": item["id"], "name": item["name"],
                             "failure_type": item["failure_type"],
                             "evidence": item["evidence"], "suggestion": item["suggestion"]})

    scored = [i for i in items if i["status"] != "skip"]
    total = sum(i["max"] for i in scored)
    got = sum(i["score"] for i in scored)
    ratio = (got / total) if total else 0.0
    return {
        "case_id": spec.get("case_id"),
        "spec_version": spec.get("spec_version"),
        "items": items,
        "failures": failures,
        "counts": {"pass": sum(1 for i in items if i["status"] == "pass"),
                   "fail": sum(1 for i in items if i["status"] == "fail"),
                   "skip": sum(1 for i in items if i["status"] == "skip"),
                   "error": sum(1 for i in items if i["status"] == "error")},
        "weight_scored": total, "weight_earned": round(got, 3),
        "ratio": round(ratio, 4),
        "band": band_of(ratio, spec.get("bands") or DEFAULT_BANDS),
        "skipped": [{"id": i["id"], "reason": i["evidence"]} for i in items if i["status"] == "skip"],
    }


def cmd_score(args) -> int:
    spec = json.load(open(args.spec, encoding="utf-8"))
    if args.sections:
        wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
        for c in spec.get("checks", []):
            if c.get("kind") == "sections":
                c["sections"] = wanted
    if args.glossary:
        glossary = json.load(open(args.glossary, encoding="utf-8"))
        spec["glossary"] = glossary
        for c in spec.get("checks", []):
            if c.get("kind") == "glossary":
                c["glossary"] = glossary
    try:
        result = json.load(open(args.result, encoding="utf-8"))
    except Exception as exc:
        payload = {"case_id": spec.get("case_id"), "error": f"unreadable result: {exc}",
                   "items": [], "counts": {"error": 1}, "ratio": 0.0, "band": "D"}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        (open(args.out, "w", encoding="utf-8").write(text) if args.out else print(text))
        return 2

    index, material_text, locators = _load_material(args)
    deliverable_text = _deliverable_text(result, args)
    payload = score_spec(spec, result, material_text, deliverable_text, index, locators)

    problems = []
    if spec.get("case_id") and result.get("case_id") != spec.get("case_id"):
        problems.append({"check": "case_id", "failure_type": "case_id_mismatch",
                         "evidence": [{"expected": spec.get("case_id"),
                                       "found": result.get("case_id")}]})
    payload["case_id_problems"] = problems
    payload["deliverable_text_chars"] = len(deliverable_text)

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(args.out)
    else:
        print(text)
    if args.failures:
        with open(args.failures, "w", encoding="utf-8") as fh:
            for f in payload["failures"]:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
            for p in problems:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(args.failures, file=sys.stderr)
    clean = not payload["failures"] and not problems and payload["counts"]["error"] == 0
    return 0 if clean else 1


# ---------------------------------------------------------------- report

def cmd_report(args) -> int:
    data = json.load(open(args.score, encoding="utf-8"))
    lines = [f"# 评测报告：{data.get('case_id')}", "",
             f"- 加权得分：**{data.get('weight_earned')} / {data.get('weight_scored')}** "
             f"（{data.get('ratio')}）",
             f"- 档位：**{data.get('band')}**",
             f"- 计数：{data.get('counts')}", ""]
    if data.get("skipped"):
        lines.append("> 未参与评分的检查（材料不足，非通过）：")
        for s in data["skipped"]:
            lines.append(f"> - {s['id']}：{s['reason']}")
        lines.append("")
    lines += ["| 检查 | 名称 | 状态 | 得分 | 失败类型 |", "|---|---|---|---|---|"]
    for item in data.get("items", []):
        lines.append(f"| {item['id']} | {item['name']} | {item['status']} | "
                     f"{item['score']}/{item['max']} | {item['failure_type'] or ''} |")
    if data.get("failures"):
        lines += ["", "## 失败项与证据", ""]
        for f in data["failures"]:
            lines.append(f"### {f['check']} · {f['failure_type']}")
            for ev in f.get("evidence", [])[:5]:
                lines.append(f"- `{json.dumps(ev, ensure_ascii=False)[:300]}`")
            if f.get("suggestion"):
                lines.append(f"- 改进建议：{f['suggestion']}")
            lines.append("")
    else:
        lines += ["", "全部检查通过。"]
    text = "\n".join(lines)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(args.out)
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- calibrate

def cmd_calibrate(args) -> int:
    a = json.load(open(args.a, encoding="utf-8"))
    b = json.load(open(args.b, encoding="utf-8"))
    bands = [label for _t, label in (a.get("bands") or DEFAULT_BANDS)] if a.get("bands") else \
        [label for _t, label in DEFAULT_BANDS]
    order = {label: i for i, label in enumerate(bands)}

    ia = {i["id"]: i for i in a.get("items", [])}
    ib = {i["id"]: i for i in b.get("items", [])}
    common = sorted(set(ia) & set(ib))
    agree, disagreements = 0, []
    for cid in common:
        sa = ia[cid]["status"] == "pass"
        sb = ib[cid]["status"] == "pass"
        if sa == sb:
            agree += 1
        else:
            disagreements.append({"id": cid, "name": ia[cid]["name"],
                                  "scorer_a": ia[cid]["status"], "scorer_b": ib[cid]["status"],
                                  "a_evidence": ia[cid].get("evidence"),
                                  "b_evidence": ib[cid].get("evidence")})
    n = len(common)
    observed = agree / n if n else None
    # Cohen's kappa on the pass/fail decision
    pa = sum(1 for c in common if ia[c]["status"] == "pass") / n if n else 0
    pb = sum(1 for c in common if ib[c]["status"] == "pass") / n if n else 0
    expected = pa * pb + (1 - pa) * (1 - pb)
    kappa = ((observed - expected) / (1 - expected)) if n and expected < 1 else None

    band_a, band_b = a.get("band"), b.get("band")
    gap = abs(order.get(band_a, 0) - order.get(band_b, 0)) if band_a in order and band_b in order else None
    verdict = ("no_decision" if n == 0 else
               "agree" if gap == 0 and not disagreements else
               "adjudicate" if gap is not None and gap <= 1 else "redesign_anchor")
    payload = {
        "case_id": a.get("case_id"),
        "items_compared": n,
        "items_only_in_a": sorted(set(ia) - set(ib)),
        "items_only_in_b": sorted(set(ib) - set(ia)),
        "exact_agreement": round(observed, 4) if observed is not None else None,
        "cohens_kappa": round(kappa, 4) if kappa is not None else None,
        "band_a": band_a, "band_b": band_b, "band_gap": gap,
        "disagreements": disagreements,
        "action": verdict,
        "rule": ("档位相同的分歧 -> 只改锚点措辞；相差一档 -> 双评裁定并记录依据；"
                 "相差两档及以上 -> 该检查的锚点不可观察，退回重写而不是改分"),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(args.out)
    else:
        print(text)
    return 0 if verdict == "agree" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Mechanical academic-writing case scorer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spec"); sp.add_argument("case"); sp.add_argument("-o", "--out")
    sp.set_defaults(fn=cmd_spec)

    sc = sub.add_parser("selfcheck"); sc.add_argument("spec"); sc.set_defaults(fn=cmd_selfcheck)

    so = sub.add_parser("score")
    so.add_argument("spec"); so.add_argument("result")
    so.add_argument("--blocks"); so.add_argument("--material"); so.add_argument("--deliverable")
    so.add_argument("--sections"); so.add_argument("--glossary")
    so.add_argument("-o", "--out"); so.add_argument("--failures")
    so.set_defaults(fn=cmd_score)

    rp = sub.add_parser("report"); rp.add_argument("score"); rp.add_argument("-o", "--out")
    rp.set_defaults(fn=cmd_report)

    ca = sub.add_parser("calibrate"); ca.add_argument("a"); ca.add_argument("b")
    ca.add_argument("-o", "--out"); ca.set_defaults(fn=cmd_calibrate)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
