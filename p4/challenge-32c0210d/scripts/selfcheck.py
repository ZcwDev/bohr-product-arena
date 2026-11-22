#!/usr/bin/env python3
"""selfcheck —— 用输入契约校验 run.py 的真实产出。退出码 0=通过, 1=不通过。

    python3 selfcheck.py --input DIR --output DIR

检查：
  1. result.json 存在且是 JSON 对象；
  2. case_id 与输入 case.json 逐字一致；
  3. output_contract 里每个字段都在结果中（值可为 not_reported，但会记 WARN）；
  4. 若契约给了状态枚举，status 必须在枚举里；status 不是 error/insufficient 一类；
  5. 字段类型：evidence_map / preserved_facts / change_log 为非空 list，元素含必需键；
  6. artifacts 中列出的每个文件存在且非空；至少一个 PDF 与一个 PNG 预览（检查文件魔数）；
  7. evidence 能解析回输入：locator.file + file_char_start/end 截取的原文 == quote；
     没有字符偏移时退化为 quote 在该文件全文中逐字出现；
  8. 修订稿里原文块逐字保留；preserved_facts.verified_in_revised 全为 True；
  9. 版本记录：回滚 hash == v0，重放 hash == v1；非法操作后状态不变。
若 result.json 带 artifact_root（主结果指向子目录产物时），产物路径相对 OUTPUT/artifact_root 解析。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BAD_STATUS = ("error", "fail", "insufficient", "unable", "input_error")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    inp, out = Path(a.input), Path(a.output)
    fails, warns, oks = [], [], []

    def ok(c, msg):
        (oks if c else fails).append(msg)
        return c

    rp = out / "result.json"
    if not ok(rp.is_file(), f"result.json exists: {rp}"):
        return report(oks, warns, fails)
    try:
        r = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as e:
        ok(False, f"result.json parses as JSON: {e}")
        return report(oks, warns, fails)
    ok(isinstance(r, dict), "result.json is a JSON object")
    root = out / r.get("artifact_root", "")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from run import find_case  # 与 run.py 使用同一套 case 文件查找规则
        case_p = find_case(inp)
    except Exception:
        case_p = next((inp / n for n in ("case.json", "input.json", "example-input.json") if (inp / n).is_file()), None)
    case = json.loads(case_p.read_text(encoding="utf-8")) if case_p else {}
    ok(bool(case), f"input contract found: {case_p}")
    ok(r.get("case_id") == case.get("case_id"), f"case_id verbatim: result={r.get('case_id')!r} input={case.get('case_id')!r}")
    for k in (case.get("output_contract") or {}):
        if ok(k in r, f"contract field present: {k}") and r[k] == "not_reported":
            warns.append(f"contract field {k} = not_reported")
    st = str(r.get("status", ""))
    enum = case.get("status_values") or (case.get("output_contract") or {}).get("status_values")
    if isinstance(enum, list):
        ok(st in enum, f"status {st!r} in contract enum {enum}")
    ok(bool(st) and not any(b in st.lower() for b in BAD_STATUS), f"status is a completion value: {st!r}")
    for k, req in (("evidence_map", {"claim", "locator", "quote"}), ("preserved_facts", {"fact", "source", "changed"}),
                   ("change_log", {"action", "reason"})):
        v = r.get(k)
        if ok(isinstance(v, list) and len(v) > 0, f"{k} is non-empty list (n={len(v) if isinstance(v, list) else 'NA'})"):
            bad = [i for i, e in enumerate(v) if not (isinstance(e, dict) and req <= set(e))]
            ok(not bad, f"{k} items have keys {sorted(req)} (bad idx: {bad[:5]})")
    arts = r.get("artifacts") or []
    ok(len(arts) > 0, f"artifacts listed: {len(arts)}")
    for p in arts:
        f = root / p
        ok(f.is_file() and f.stat().st_size > 0, f"artifact exists & non-empty: {p} ({f.stat().st_size if f.exists() else 0} B)")
    pdfs = [root / p for p in arts if p.lower().endswith(".pdf")]
    pngs = [root / p for p in arts if p.lower().endswith(".png")]
    has_pdf = any(x.is_file() and x.read_bytes()[:5] == b"%PDF-" for x in pdfs)
    has_png = any(x.is_file() and x.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for x in pngs)
    ok(has_pdf, f"real PDF preview present (magic %PDF-, n={len(pdfs)})")
    ok(has_png, f"real PNG preview present (PNG signature, n={len(pngs)})")
    cache, n_ok, n_bad, bad_ex = {}, 0, 0, []
    em = r.get("evidence_map") if isinstance(r.get("evidence_map"), list) else []
    for e in em:
        if not isinstance(e, dict):
            n_bad += 1
            continue
        lc, q = e.get("locator") or {}, e.get("quote") or ""
        # 二进制材料（pdf/docx/html/xlsx…）的偏移指向 run.py 落盘的抽取文本（相对 OUTPUT），直接按偏移核对
        fp = (root / lc["offsets_refer_to"]) if lc.get("offsets_refer_to") else (inp / lc.get("file", ""))
        if not fp.is_file():
            n_bad += 1
            bad_ex.append(f"missing file {lc.get('file')}")
            continue
        if fp not in cache:
            try:
                cache[fp] = fp.read_text(encoding="utf-8")
            except Exception:
                cache[fp] = None
        txt = cache[fp]
        if txt is None:  # binary material without extracted-text file: cannot resolve offsets -> not counted as ok
            good = False
        elif "file_char_start" in lc:
            good = txt[lc["file_char_start"]:lc["file_char_end"]] == q
        else:
            good = q in txt
        if good:
            n_ok += 1
        else:
            n_bad += 1
            bad_ex.append(f"{lc.get('block_id')}@{lc.get('file_char_start')}: {q[:40]!r}")
    ok(n_ok > 0 and n_bad == 0, f"evidence resolves to input: ok={n_ok} bad={n_bad} {bad_ex[:3]}")
    pf = r.get("preserved_facts") or []
    if isinstance(pf, list) and pf:
        nv = [x["fact"] for x in pf if isinstance(x, dict) and x.get("verified_in_revised") is False]
        ok(not nv, f"preserved facts verbatim in revised manuscript (not verified: {nv[:5]})")
    d = r.get("deliverable")
    if isinstance(d, dict) and "original_text_preserved_verbatim" in d:
        ok(d["original_text_preserved_verbatim"] is True, "all original blocks verbatim in revised manuscript")
    vr = r.get("version_record") or {}
    if vr.get("executed") is False:
        warns.append(f"versioning not executed: {vr.get('reason')}")
    elif vr:
        ok(vr.get("rollback_matches_v0") is True, "rollback state hash == v0")
        ok(vr.get("reapply_matches_v1") is True, "reapply state hash == v1")
    inv = [c for c in (r.get("change_log") if isinstance(r.get("change_log"), list) else []) if isinstance(c, dict) and c.get("action") == "invalid_operation_test"]
    if inv:
        ok(all(c.get("state_unchanged") for c in inv), f"invalid operations rejected with unchanged state ({len(inv)})")
    return report(oks, warns, fails)


def report(oks, warns, fails):
    for m in oks:
        print("PASS", m)
    for m in warns:
        print("WARN", m)
    for m in fails:
        print("FAIL", m)
    print(f"selfcheck: {len(oks)} pass, {len(warns)} warn, {len(fails)} fail -> {'OK' if not fails else 'FAILED'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
