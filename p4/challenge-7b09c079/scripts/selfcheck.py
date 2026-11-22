#!/usr/bin/env python3
"""Validate the REAL outputs of run.py against the REAL input (no synthetic cases).

    python3 selfcheck.py --input /input --output /app/output [--quiet]

Checks (each reported as PASS/FAIL with detail):
  C1 result.json exists and parses
  C2 every key of case.json.output_contract is present (value may be 'not_reported')
  C3 case_id equals case.json case_id verbatim
  C4 types: evidence_map/change_log/preserved_facts are list or 'not_reported'; deliverable dict/str
  C5 every evidence locator (result.evidence, evidence_map, change_log[].evidence/anchor, preserved_facts)
     resolves into an input file, and its quote occurs in the resolved region
  C6 every artifact listed in deliverable.files exists, is non-empty, sha256 matches
  C7 at least one editable file (.docx/.md/.tex/.txt/.json) and one rendered preview (.pdf/.png);
     PNG/PDF magic bytes valid, DOCX parseable
  C8 version proofs claimed true are re-verified from the files (rollback == v1, reapplied == v2)
  C9 every invalid-operation record shows outcome=refused and state_unchanged=true (re-hashed)
 C10 not_reported values are explained in uncertainty/notes
Exit code 0 if all checks pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wk_io  # noqa: E402

NR = "not_reported"


def find_case(inp: Path):
    c = inp / "case.json"
    if c.is_file():
        return c
    for p in sorted(inp.rglob("*.json")):
        try:
            o = json.loads(wk_io.read_text_file(p))
        except Exception:
            continue
        if isinstance(o, dict) and "case_id" in o:
            return p
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    inp, out = Path(a.input).resolve(), Path(a.output).resolve()
    checks = []

    def chk(cid, ok, detail):
        checks.append({"check": cid, "ok": bool(ok), "detail": detail})

    rp = out / "result.json"
    try:
        res = json.loads(rp.read_text(encoding="utf-8"))
        chk("C1 result.json parses", True, str(rp))
    except Exception as exc:  # noqa: BLE001
        chk("C1 result.json parses", False, repr(exc))
        return report(checks)

    cp = find_case(inp)
    case = json.loads(wk_io.read_text_file(cp)) if cp else {}
    contract = case.get("output_contract") or {}
    missing = [k for k in contract if k not in res]
    chk("C2 output_contract keys present", cp is not None and not missing,
        f"case={cp.name if cp else None}; keys={list(contract)}; missing={missing}")
    chk("C3 case_id verbatim", case.get("case_id") is not None and res.get("case_id") == case.get("case_id"),
        f"case={case.get('case_id')!r} result={res.get('case_id')!r}")

    tp = []
    for k in ("evidence_map", "change_log", "preserved_facts"):
        if k in res and not (isinstance(res[k], list) or res[k] == NR):
            tp.append(f"{k} is {type(res[k]).__name__}")
    if "deliverable" in res and not isinstance(res["deliverable"], (dict, str)):
        tp.append("deliverable type")
    chk("C4 field types", not tp, tp or "ok")

    rows = []
    for r in res.get("evidence") or []:
        rows.append(("evidence", r))
    for r in res.get("evidence_map") if isinstance(res.get("evidence_map"), list) else []:
        rows.append(("evidence_map", r))
    for c in res.get("change_log") if isinstance(res.get("change_log"), list) else []:
        for e in c.get("evidence") or []:
            rows.append(("change_log.evidence", e))
        if c.get("anchor_locator"):
            rows.append(("change_log.anchor", {"locator": c["anchor_locator"]}))
    for f in res.get("preserved_facts") if isinstance(res.get("preserved_facts"), list) else []:
        rows.append(("preserved_facts", {"locator": f.get("locator"), "quote": f.get("fact") if f.get("type") else None}))
    bad, cache = [], {}
    for src, r in rows:
        loc = r.get("locator")
        if not isinstance(loc, dict):
            bad.append(f"{src}: missing locator")
            continue
        try:
            region = wk_io.resolve_locator(inp, loc, cache)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{src}: {wk_io.locator_str(loc)} -> {exc!r}")
            continue
        if "char_start" in loc:
            region = region[loc["char_start"]:loc["char_end"]]
            if not region.strip():
                bad.append(f"{src}: empty char span {wk_io.locator_str(loc)}")
                continue
        q = r.get("quote")
        if q is None and r.get("value") is not None:
            q = str(r["value"])
        if q and not wk_io.quote_in(region, str(q)):
            bad.append(f"{src}: quote not found at {wk_io.locator_str(loc)}: {str(q)[:60]!r}")
    chk("C5 evidence locators resolve into input", rows and not bad,
        f"{len(rows)} locators checked; failures={bad[:10]}")

    files = res.get("deliverable", {}).get("files", []) if isinstance(res.get("deliverable"), dict) else []
    abad = []
    for f in files:
        p = out / f["path"]
        if not p.is_file() or p.stat().st_size == 0:
            abad.append(f"{f['path']}: missing/empty")
        elif f.get("sha256") and hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            abad.append(f"{f['path']}: sha256 mismatch")
    chk("C6 listed artifacts exist, non-empty, hash ok", files and not abad, f"{len(files)} files; failures={abad}")

    paths = [out / f["path"] for f in files]
    editable = [p for p in paths if p.suffix.lower() in (".docx", ".md", ".tex", ".txt", ".json") and p.is_file()]
    previews = [p for p in paths if p.suffix.lower() in (".pdf", ".png") and p.is_file()]
    magic_bad = []
    for p in previews:
        head = p.read_bytes()[:8]
        if p.suffix.lower() == ".png" and head != b"\x89PNG\r\n\x1a\n":
            magic_bad.append(p.name)
        if p.suffix.lower() == ".pdf" and not head.startswith(b"%PDF"):
            magic_bad.append(p.name)
    for p in editable:
        if p.suffix == ".docx":
            try:
                wk_io.docx_paragraphs(p)
            except Exception as exc:  # noqa: BLE001
                magic_bad.append(f"{p.name}: {exc!r}")
    chk("C7 editable artifact + real preview", editable and previews and not magic_bad,
        f"editable={len(editable)} previews={[p.name for p in previews]} bad={magic_bad}")

    vbad, vn = [], 0
    for v in res.get("version_control") if isinstance(res.get("version_control"), list) else []:
        if v.get("rollback_equals_v1") and v.get("v1") and v.get("rolled_back"):
            vn += 1
            a1, a2 = out / v["v1"], out / v["rolled_back"]
            if a1.suffix == ".docx":
                same = [x["text"] for x in wk_io.docx_paragraphs(a1)] == [x["text"] for x in wk_io.docx_paragraphs(a2)]
            else:
                same = a1.read_bytes() == a2.read_bytes() or wk_io.read_text_file(a1) == wk_io.read_text_file(a2)
            if not same:
                vbad.append(f"{v['manuscript']}: rollback != v1")
        if v.get("reapply_equals_v2") and v.get("v2") and v.get("reapplied"):
            if (out / v["v2"]).read_bytes() != (out / v["reapplied"]).read_bytes():
                vbad.append(f"{v['manuscript']}: reapplied != v2")
    chk("C8 version proofs re-verified", not vbad,
        f"{vn} rollback claims re-checked; failures={vbad}" if vn else "no version claims (no manuscript edited)")

    fl = res.get("invalid_operations") if isinstance(res.get("invalid_operations"), list) else []
    fbad = [f["operation"] for f in fl if f.get("outcome") != "refused" or not f.get("state_unchanged")]
    for f in fl:
        sp = out / f["state_file"]
        if sp.is_file() and f.get("sha256_after") and hashlib.sha256(sp.read_bytes()).hexdigest() != f["sha256_after"]:
            fbad.append(f"{f['operation']}: state file changed after test")
    chk("C9 invalid operations refused, state unchanged", not fbad, f"{len(fl)} tests; failures={fbad}")

    nr = [k for k in contract if res.get(k) == NR]
    unexplained = [k for k in nr if not res.get("uncertainty")]
    chk("C10 not_reported values explained", not unexplained, f"not_reported fields={nr}; unexplained={unexplained}")
    return report(checks)


def report(checks) -> int:
    ok = all(c["ok"] for c in checks)
    print(json.dumps({"selfcheck": "PASS" if ok else "FAIL", "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
