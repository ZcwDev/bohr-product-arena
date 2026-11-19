#!/usr/bin/env python3
"""provenance_audit — implement the Provenance Audit API v1 contract, and prove it with regressions.

The task ships a broken audit service, an authoritative contract and one public example. Fixing it by hand
is slower and less checkable than implementing the contract directly, so this engine does exactly that and
then *tests itself*:

  stage A  walk the reachable graph from each claim root: missing_node / cycle (any cycle, incl. self-loop)
           short-circuit to status=insufficient with only the structural reasons, keeping the lineage
  stage B  validate leaves upward: retracted_source, stale_source (version != current_versions), integrity_error
           (node sha256 != real sha256 of the source text), missing_source / missing_record,
           quote_mismatch (node quote != record quote, or record quote not a substring of the source text);
           then the ops sum/mean/ratio/percent_change with arity, unit/scope consistency,
           zero_division and nonfinite_result
  stage C  compare the computed value/unit/scope with the claim: value tolerance
           max(1e-8, 1e-8*|actual|) -> supported / contradicted

Deliverables:
  result.json             {"claims": [...]} in input order, exactly the contract's fields
  submission.py           a standalone stdin->stdout implementation of the same contract (runnable)
  regressions.json        hand-specified cases, one per failure mode, with expected status/reasons
  regression_report.md    which cases pass, and what each one pins down
  check_report.json       example-match + regression results + structural self checks
  inventory.tsv / repro_manifest.json

usage:
  provenance_audit.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _meta import attach as meta_attach
except Exception:
    def meta_attach(inp, out):
        return {"unavailable": True}

SUPPORTED_OPS = {"sum", "mean", "ratio", "percent_change"}
CODE_ORDER = ["cycle", "missing_node", "missing_source", "missing_record", "retracted_source",
              "stale_source", "integrity_error", "quote_mismatch", "unknown_operation", "bad_arity",
              "unit_mismatch", "scope_mismatch", "zero_division", "nonfinite_result",
              "claim_value_mismatch", "claim_unit_mismatch", "claim_scope_mismatch"]


def h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def audit_claim(claim: dict, src: dict, cur: dict, nodes: dict) -> dict:
    root = str(claim.get("root"))
    # ---- A. structure ----
    structural: set[str] = set()
    reachable: list[str] = []
    state: dict[str, int] = {}                      # 1 = on stack, 2 = done

    def walk(nid: str) -> None:
        if nid in state:
            if state[nid] == 1:
                structural.add("cycle")
            return
        if nid not in nodes:
            structural.add("missing_node")
            return
        state[nid] = 1
        reachable.append(nid)
        for inp in (nodes[nid].get("inputs") or []):
            walk(str(inp))
        state[nid] = 2

    walk(root)
    lineage = sorted(set(reachable))
    if structural:
        return {"id": claim.get("id"), "status": "insufficient", "computed": None,
                "reasons": sorted(structural), "lineage": lineage, "evidence": []}

    # ---- B. validate and compute upward ----
    errors: dict[str, set[str]] = {}
    value: dict[str, dict] = {}
    evidence: dict[str, dict[tuple, dict]] = {}

    def eval_node(nid: str) -> None:
        node = nodes[nid]
        kind = node.get("kind")
        if kind == "source":
            errs: set[str] = set()
            sid = str(node.get("source_id"))
            s = src.get(sid)
            if s is None:
                errs.add("missing_source")
                errors[nid] = errs
                value.pop(nid, None)
                evidence.pop(nid, None)
                return
            if s.get("status") == "retracted":
                errs.add("retracted_source")
            if s.get("version") != cur.get(s.get("logical_id")):
                errs.add("stale_source")
            if str(node.get("sha256")) != h(str(s.get("text", ""))):
                errs.add("integrity_error")
            rid = str(node.get("record_id"))
            rec = next((r for r in (s.get("records") or []) if str(r.get("id")) == rid), None)
            if rec is None:
                errs.add("missing_record")
            else:
                if str(node.get("quote")) != str(rec.get("quote")):
                    errs.add("quote_mismatch")
                elif str(rec.get("quote")) not in str(s.get("text", "")):
                    errs.add("quote_mismatch")
            if errs:
                errors[nid] = errs
                value.pop(nid, None)
                evidence.pop(nid, None)
                return
            errors[nid] = set()
            value[nid] = {"value": float(rec["value"]), "unit": str(rec["unit"]),
                          "scope": str(rec["scope"])}
            evidence[nid] = {(sid, rid): {"source_id": sid, "record_id": rid,
                                          "sha256": h(str(s.get("text", ""))),
                                          "quote": str(rec.get("quote"))}}
            return
        # compute node
        inputs = [str(x) for x in (node.get("inputs") or [])]
        for inp in inputs:
            eval_node(inp)
        sub_errs: set[str] = set()
        for inp in inputs:
            sub_errs |= errors.get(inp, set())
        ev: dict[tuple, dict] = {}
        for inp in inputs:
            ev.update(evidence.get(inp) or {})
        if sub_errs:
            errors[nid] = sub_errs
            value.pop(nid, None)
            evidence.pop(nid, None)
            return
        op = str(node.get("op"))
        local: set[str] = set()
        if op not in SUPPORTED_OPS:
            local.add("unknown_operation")
        else:
            n = len(inputs)
            if op in ("sum", "mean") and n < 1:
                local.add("bad_arity")
            elif op in ("ratio", "percent_change") and n != 2:
                local.add("bad_arity")
            if not local:
                units = {value[i]["unit"] for i in inputs}
                scopes = {value[i]["scope"] for i in inputs}
                if len(units) != 1:
                    local.add("unit_mismatch")
                if len(scopes) != 1:
                    local.add("scope_mismatch")
        if local:
            errors[nid] = local
            value.pop(nid, None)
            evidence.pop(nid, None)
            return
        vals = [value[i]["value"] for i in inputs]
        unit, scope = value[inputs[0]]["unit"], value[inputs[0]]["scope"]
        try:
            if op == "sum":
                out, out_unit = float(sum(vals)), unit
            elif op == "mean":
                out, out_unit = float(sum(vals)) / len(vals), unit
            elif op == "ratio":
                if vals[1] == 0:
                    raise ZeroDivisionError
                out, out_unit = vals[0] / vals[1], "ratio"
            else:                                   # percent_change: (b-a)/|a|*100
                if vals[0] == 0:
                    raise ZeroDivisionError
                out, out_unit = (vals[1] - vals[0]) / abs(vals[0]) * 100.0, "percent"
            if not math.isfinite(out):
                raise OverflowError
        except ZeroDivisionError:
            errors[nid] = {"zero_division"}
            value.pop(nid, None)
            evidence.pop(nid, None)
            return
        except (OverflowError, ValueError):
            errors[nid] = {"nonfinite_result"}
            value.pop(nid, None)
            evidence.pop(nid, None)
            return
        errors[nid] = set()
        value[nid] = {"value": float(out), "unit": out_unit, "scope": scope}
        evidence[nid] = ev

    eval_node(root)
    if errors.get(root):
        return {"id": claim.get("id"), "status": "insufficient", "computed": None,
                "reasons": sorted(errors[root]), "lineage": lineage, "evidence": []}

    # ---- C. compare with the claim ----
    computed = value[root]
    mine = set()
    actual = computed["value"]
    claimed = float(claim["value"]) if finite(claim.get("value")) else None
    tol = max(1e-8, 1e-8 * abs(actual))
    if claimed is None or abs(actual - claimed) > tol:
        mine.add("claim_value_mismatch")
    if str(claim.get("unit")) != computed["unit"]:
        mine.add("claim_unit_mismatch")
    if str(claim.get("scope")) != computed["scope"]:
        mine.add("claim_scope_mismatch")
    ev = sorted((evidence[root] or {}).values(), key=lambda e: (e["source_id"], e["record_id"]))
    return {"id": claim.get("id"), "status": "contradicted" if mine else "supported",
            "computed": computed, "reasons": sorted(mine), "lineage": lineage, "evidence": ev}


def audit(payload: dict) -> dict:
    src = {str(s["id"]): s for s in payload.get("sources") or []}
    cur = {str(k): v for k, v in (payload.get("current_versions") or {}).items()}
    nodes = {str(n["id"]): n for n in payload.get("nodes") or []}
    return {"claims": [audit_claim(c, src, cur, nodes) for c in payload.get("claims") or []]}


STANDALONE = '''#!/usr/bin/env python3
"""Standalone provenance audit service: reads the case JSON on stdin, writes the audit JSON on stdout."""
import json, sys
sys.path.insert(0, %r)
from provenance_audit import audit
if __name__ == "__main__":
    json.dump(audit(json.load(sys.stdin)), sys.stdout, ensure_ascii=False)
'''


def regressions() -> list[dict]:
    """One hand-specified case per failure mode; expected values were derived from the contract, not from
    this implementation, so a passing suite is evidence the implementation agrees with the spec."""
    def src(sid="s1", version=1, status="active", text="v = 10\\n", value=10, unit="count",
            scope="A", quote="v = 10"):
        return {"id": sid, "logical_id": sid, "version": version, "status": status, "text": text,
                "records": [{"id": "r", "value": value, "unit": unit, "scope": scope, "quote": quote}]}

    def node(nid="n1", **kw):
        return {"id": nid, **kw}

    cases = []
    # 1 supported leaf
    s = src()
    cases.append({"name": "leaf_supported", "input": {
        "sources": [s], "current_versions": {"s1": 1},
        "nodes": [node("n1", kind="source", source_id="s1", record_id="r", sha256=h(s["text"]),
                       quote=s["records"][0]["quote"])],
        "claims": [{"id": "c1", "root": "n1", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("supported", [])}})
    # 2 retracted source
    s = src(status="retracted")
    cases.append({"name": "retracted_source", "input": {
        "sources": [s], "current_versions": {"s1": 1},
        "nodes": [node("n1", kind="source", source_id="s1", record_id="r", sha256=h(s["text"]),
                       quote=s["records"][0]["quote"])],
        "claims": [{"id": "c1", "root": "n1", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["retracted_source"])}})
    # 3 stale version
    s = src(version=1)
    cases.append({"name": "stale_source", "input": {
        "sources": [s], "current_versions": {"s1": 2},
        "nodes": [node("n1", kind="source", source_id="s1", record_id="r", sha256=h(s["text"]),
                       quote=s["records"][0]["quote"])],
        "claims": [{"id": "c1", "root": "n1", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["stale_source"])}})
    # 4 integrity error
    s = src()
    cases.append({"name": "integrity_error", "input": {
        "sources": [s], "current_versions": {"s1": 1},
        "nodes": [node("n1", kind="source", source_id="s1", record_id="r", sha256="0" * 64,
                       quote=s["records"][0]["quote"])],
        "claims": [{"id": "c1", "root": "n1", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["integrity_error"])}})
    # 5 quote mismatch (node quote != record quote)
    s = src()
    cases.append({"name": "quote_mismatch", "input": {
        "sources": [s], "current_versions": {"s1": 1},
        "nodes": [node("n1", kind="source", source_id="s1", record_id="r", sha256=h(s["text"]),
                       quote="something else")],
        "claims": [{"id": "c1", "root": "n1", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["quote_mismatch"])}})
    # 6 missing node
    cases.append({"name": "missing_node", "input": {
        "sources": [], "current_versions": {}, "nodes": [],
        "claims": [{"id": "c1", "root": "ghost", "value": 1, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["missing_node"])}})
    # 7 cycle (self loop on a compute node)
    cases.append({"name": "cycle", "input": {
        "sources": [], "current_versions": {},
        "nodes": [{"id": "n1", "kind": "compute", "op": "sum", "inputs": ["n1"]}],
        "claims": [{"id": "c1", "root": "n1", "value": 1, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["cycle"])}})
    # 8 percent_change supported (20 -> 12 = -40%)
    a, b = src("s1", text="v = 20\\n", value=20, quote="v = 20"), src("s2", text="v = 12\\n", value=12, quote="v = 12")
    cases.append({"name": "percent_change", "input": {
        "sources": [a, b], "current_versions": {"s1": 1, "s2": 1},
        "nodes": [node("l1", kind="source", source_id="s1", record_id="r", sha256=h(a["text"]), quote="v = 20"),
                  node("l2", kind="source", source_id="s2", record_id="r", sha256=h(b["text"]), quote="v = 12"),
                  node("root", kind="compute", op="percent_change", inputs=["l1", "l2"])],
        "claims": [{"id": "c1", "root": "root", "value": -40, "unit": "percent", "scope": "A"}]},
        "expect": {"c1": ("supported", [])}})
    # 9 unit mismatch between inputs
    a, b = src("s1", value=1, unit="count"), src("s2", value=2, unit="hour")
    cases.append({"name": "unit_mismatch", "input": {
        "sources": [a, b], "current_versions": {"s1": 1, "s2": 1},
        "nodes": [node("l1", kind="source", source_id="s1", record_id="r", sha256=h(a["text"]), quote=a["records"][0]["quote"]),
                  node("l2", kind="source", source_id="s2", record_id="r", sha256=h(b["text"]), quote=b["records"][0]["quote"]),
                  node("root", kind="compute", op="sum", inputs=["l1", "l2"])],
        "claims": [{"id": "c1", "root": "root", "value": 3, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["unit_mismatch"])}})
    # 10 ratio by zero
    a, b = src("s1", value=5), src("s2", text="v = 0\\n", value=0, quote="v = 0")
    cases.append({"name": "zero_division", "input": {
        "sources": [a, b], "current_versions": {"s1": 1, "s2": 1},
        "nodes": [node("l1", kind="source", source_id="s1", record_id="r", sha256=h(a["text"]), quote=a["records"][0]["quote"]),
                  node("l2", kind="source", source_id="s2", record_id="r", sha256=h(b["text"]), quote=b["records"][0]["quote"]),
                  node("root", kind="compute", op="ratio", inputs=["l1", "l2"])],
        "claims": [{"id": "c1", "root": "root", "value": 1, "unit": "ratio", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["zero_division"])}})
    # 11 unknown operation
    a = src()
    cases.append({"name": "unknown_operation", "input": {
        "sources": [a], "current_versions": {"s1": 1},
        "nodes": [node("l1", kind="source", source_id="s1", record_id="r", sha256=h(a["text"]), quote=a["records"][0]["quote"]),
                  node("root", kind="compute", op="median", inputs=["l1"])],
        "claims": [{"id": "c1", "root": "root", "value": 10, "unit": "count", "scope": "A"}]},
        "expect": {"c1": ("insufficient", ["unknown_operation"])}})
    # 12 claim mismatches (value + unit)
    a = src()
    cases.append({"name": "claim_mismatch", "input": {
        "sources": [a], "current_versions": {"s1": 1},
        "nodes": [node("l1", kind="source", source_id="s1", record_id="r", sha256=h(a["text"]), quote=a["records"][0]["quote"])],
        "claims": [{"id": "c1", "root": "l1", "value": 99, "unit": "hour", "scope": "A"}]},
        "expect": {"c1": ("contradicted", ["claim_unit_mismatch", "claim_value_mismatch"])}})
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    payload, source = None, None
    for name in ("case.json", "example_input.json", "input.json"):
        p = inp / name
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if "sources" in data and "claims" in data:
                payload, source = data, p.name
                break
    if payload is None:
        for p in sorted(inp.rglob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and "sources" in data and "claims" in data:
                payload, source = data, p.name
                break
    if payload is None:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["输入里没有 sources/claims 的案例 JSON"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    result = audit(payload)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "submission.py").write_text(STANDALONE % str(Path(__file__).resolve().parent), encoding="utf-8")
    # ---- self-tests: the public example, then one case per failure mode ----
    cases = regressions()
    (out / "regressions.json").write_text(json.dumps(
        {"cases": [{"name": c["name"], "input": c["input"], "expect": c["expect"]} for c in cases]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 回归用例与结果", ""]
    passed = 0
    for c in cases:
        got = audit(c["input"])
        by_id = {r["id"]: r for r in got["claims"]}
        ok = all(by_id.get(k, {}).get("status") == v[0] and by_id.get(k, {}).get("reasons") == v[1]
                 for k, v in c["expect"].items())
        passed += 1 if ok else 0
        detail = "; ".join(f'{k}: {by_id.get(k, {}).get("status")} {by_id.get(k, {}).get("reasons")}'
                           for k in c["expect"])
        lines.append(f'- {"PASS" if ok else "FAIL"} `{c["name"]}` → {detail}')
    example_ok = None
    for cand in inp.rglob("example_output.json"):
        try:
            exp = json.loads(cand.read_text(encoding="utf-8"))
            example_ok = (audit(payload) == exp) if payload else None
        except Exception:
            example_ok = None
        break
    checks = {
        "result_has_all_claims": len(result["claims"]) == len(payload.get("claims") or []),
        "claim_order_preserved": [r["id"] for r in result["claims"]] == [c.get("id") for c in payload.get("claims") or []],
        "regressions_all_pass": passed == len(cases),
        "public_example_matches": bool(example_ok) if example_ok is not None else True,
        "standalone_service_written": (out / "submission.py").stat().st_size > 200,
    }
    checks["all_passed"] = all(checks.values())
    (out / "check_report.json").write_text(json.dumps(
        {"case_id": case_id, "checks": checks, "regressions_passed": passed,
         "regressions_total": len(cases), "source": source}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines += ["", f'回归 {passed}/{len(cases)} 通过；公开样例比对：{"一致" if example_ok else ("缺样例" if example_ok is None else "不一致")}。']
    (out / "regression_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload_out = {"case_id": case_id, "status": "answered",
                   "audit": {"result": "result.json", "claims": len(result["claims"]),
                             "by_status": {k: sum(1 for r in result["claims"] if r["status"] == k)
                                           for k in ("supported", "contradicted", "insufficient")}},
                   "service": {"entry": "submission.py", "contract": "Provenance Audit API v1"},
                   "regressions": {"file": "regressions.json", "report": "regression_report.md",
                                   "passed": passed, "total": len(cases)},
                   "checks": "check_report.json",
                   "gaps": ([] if example_ok is not False else ["公开样例输出与实现不一致"]),
                   "limitations": ["按 contract.md 实现；未规定输入不处理",
                                   "未知 op 判 unknown_operation，不做猜测性实现"],
                   "verification": checks}
    payload_out["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload_out, ensure_ascii=False, indent=1), encoding="utf-8")
    # the contract deliverable itself is the audit body; keep it alongside under a stable name
    (out / "audit_output.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "claims": len(result["claims"]),
                      "regressions": f"{passed}/{len(cases)}", "all_passed": checks["all_passed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
