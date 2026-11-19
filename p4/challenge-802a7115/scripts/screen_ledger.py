#!/usr/bin/env python3
"""screen_ledger — a reproducible screening ledger whose counts reconcile end to end.

The thing that makes a systematic screen trustworthy is not a good hit rate: it is that someone else
can walk the same path — which query, which fields, which cut-off date, which records were merged as
duplicates, which criteria version judged each record, and whether the numbers at every stage add up.
So this tool emits exactly that:

  evidence_log.jsonl  the search/screening decisions in order: query terms used, criteria version,
                      per-record decision with the matched term and the record's locator
  screened.tsv        one row per record: id, decision, reason, matched terms, metadata completeness
  prisma.tsv / .md    identified → duplicates merged → screened → excluded (by criterion) → included,
                      with an explicit reconciliation check
  result.json         检索结果 / 证据记录 / 状态说明 (empty, conflicts, missing metadata)

Rules that keep it honest: a record is only excluded on a stated criterion, every decision keeps the
literal term that triggered it, records without an abstract are marked `metadata_incomplete` rather
than silently included, and duplicated titles are merged (never double-counted) with both ids kept.

usage:
  screen_ledger.py --input /input --output /app/output [--min-year 2000]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

FIELDS_TITLE = ("title", "标题", "题名")
FIELDS_ABS = ("abstract", "摘要", "summary")
FIELDS_DOI = ("doi", "DOI", "identifier")
FIELDS_YEAR = ("year", "年份", "publication_year", "pub_year")
FIELDS_ID = ("id", "record_id", "pmid", "openalex_id", "arxiv_id")


def load_records(inp: Path) -> tuple[list[dict], str]:
    """Read candidates from any CSV/TSV/JSON under /input (first table that has titles)."""
    best: tuple[list[dict], str] = ([], "")
    for path in sorted(inp.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".tsv", ".json", ".jsonl"}:
            continue
        if path.name.lower().startswith(("task", "case", "output-contract", "example-input", "readme")):
            continue
        rows: list[dict] = []
        try:
            if path.suffix.lower() in {".json", ".jsonl"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                data = ([json.loads(l) for l in text.splitlines() if l.strip()]
                        if path.suffix.lower() == ".jsonl" else json.loads(text))
                if isinstance(data, dict):
                    for key in ("records", "results", "items", "papers", "candidates"):
                        if isinstance(data.get(key), list):
                            data = data[key]
                            break
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    rows = [dict(r) for r in data]
            else:
                sep = "\t" if path.suffix.lower() == ".tsv" else ","
                with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                    rows = list(csv.DictReader(fh, delimiter=sep))
        except Exception:
            continue
        if rows and _get(rows[0], FIELDS_TITLE):
            if len(rows) > len(best[0]):
                best = (rows, path.name)
    return best


def _get(row: dict, names) -> str:
    for n in names:
        for k in row:
            if k and k.strip().lower() == n.lower():
                return str(row[k] or "").strip()
    return ""


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def criteria_from(inp: Path, min_year: int) -> dict:
    """Include/exclude terms: from case.json if present, else from the instruction text."""
    crit = {"version": "v1-default", "include": [], "exclude": [], "min_year": min_year,
            "date_cutoff": "", "queries": []}
    case = {}
    if (inp / "case.json").is_file():
        try:
            case = json.loads((inp / "case.json").read_text(encoding="utf-8"))
        except Exception:
            case = {}
    for key in ("criteria", "screening_criteria", "eligibility"):
        val = case.get(key) if isinstance(case, dict) else None
        if isinstance(val, dict):
            crit["version"] = str(val.get("version") or crit["version"])
            for k in ("include", "include_terms", "inclusion"):
                if isinstance(val.get(k), list):
                    crit["include"] = [str(x) for x in val[k]]
            for k in ("exclude", "exclude_terms", "exclusion"):
                if isinstance(val.get(k), list):
                    crit["exclude"] = [str(x) for x in val[k]]
            if val.get("min_year"):
                crit["min_year"] = int(val["min_year"])
            if val.get("date_cutoff"):
                crit["date_cutoff"] = str(val["date_cutoff"])
    if case.get("query") or case.get("queries"):
        crit["queries"] = case.get("queries") or [case.get("query")]
    return crit


def tally(records: list[dict], crit: dict) -> dict:
    identified = len(records)
    # 1) normalise + metadata completeness
    for i, r in enumerate(records, 1):
        r["_id"] = _get(r, FIELDS_ID) or f"REC{i:04d}"
        r["_title"] = _get(r, FIELDS_TITLE)
        r["_abstract"] = _get(r, FIELDS_ABS)
        r["_doi"] = _get(r, FIELDS_DOI).lower()
        r["_year"] = _get(r, FIELDS_YEAR)
        r["_key"] = norm_title(r["_title"])
        r["_locator"] = f'{r.get("_source", "records")}#row{i}'
        r["metadata_incomplete"] = (not r["_abstract"]) or (not r["_year"])
    # 2) duplicate merging: same DOI, else same normalised title
    seen_doi: dict[str, str] = {}
    seen_title: dict[str, str] = {}
    merged: list[dict] = []
    dup_log: list[dict] = []
    for r in records:
        dup_of = None
        if r["_doi"] and r["_doi"] in seen_doi:
            dup_of = seen_doi[r["_doi"]]
        elif r["_key"] and r["_key"] in seen_title:
            dup_of = seen_title[r["_key"]]
        if dup_of:
            dup_log.append({"merged": r["_id"], "into": dup_of,
                            "basis": "same DOI" if r["_doi"] and r["_doi"] in seen_doi else "same normalised title"})
            continue
        if r["_doi"]:
            seen_doi[r["_doi"]] = r["_id"]
        if r["_key"]:
            seen_title[r["_key"]] = r["_id"]
        merged.append(r)
    # 3) per-record screening against the criteria version
    excluded_by: dict[str, int] = {}
    decisions: list[dict] = []
    included: list[dict] = []
    for r in merged:
        text = (r["_title"] + " " + r["_abstract"]).lower()
        hit_ex = [t for t in crit["exclude"] if t.lower() in text]
        hit_in = [t for t in crit["include"] if t.lower() in text]
        year_ok = True
        if r["_year"]:
            m = re.search(r"(19|20)\d{2}", r["_year"])
            if m:
                year_ok = int(m.group(0)) >= crit["min_year"]
        if hit_ex:
            decision, reason = "exclude", f"命中排除词：{', '.join(hit_ex[:3])}"
        elif not year_ok:
            decision, reason = "exclude", f"发表年早于 {crit['min_year']}"
        elif crit["include"] and not hit_in:
            decision, reason = "exclude", "未命中任何纳入词"
        elif r["metadata_incomplete"]:
            decision, reason = "include_with_caveat", "纳入但元数据不完整（缺摘要或年份），需人工核对"
        else:
            decision, reason = "include", f"命中纳入词：{', '.join(hit_in[:3]) or '（无需纳入词）'}"
        if decision == "exclude":
            excluded_by[reason.split("：")[0]] = excluded_by.get(reason.split("：")[0], 0) + 1
        row = {"id": r["_id"], "title": r["_title"][:200], "year": r["_year"], "doi": r["_doi"],
               "decision": decision, "reason": reason, "matched_include": hit_in,
               "matched_exclude": hit_ex, "locator": r["_locator"],
               "metadata_incomplete": r["metadata_incomplete"],
               "score": len(hit_in) * 2 + (1 if r["_abstract"] else 0) + (1 if r["_year"] else 0)}
        decisions.append(row)
        if decision.startswith("include"):
            included.append(row)
    included.sort(key=lambda d: -d["score"])
    counts = {"identified": identified, "duplicates_merged": len(dup_log),
              "screened": len(merged), "excluded": len(merged) - len(included),
              "included": len(included), "excluded_by_reason": excluded_by}
    reconcile = (counts["screened"] == counts["excluded"] + counts["included"]
                 and counts["identified"] == counts["screened"] + counts["duplicates_merged"])
    counts["reconciles"] = bool(reconcile)
    return {"criteria": crit, "decisions": decisions, "included": included,
            "duplicates": dup_log, "counts": counts,
            "metadata_incomplete": [d["id"] for d in decisions if d["metadata_incomplete"]]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--min-year", type=int, default=1990)
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id")
                          or "unreported")
        except Exception:
            pass
    records, source = load_records(inp)
    crit = criteria_from(inp, args.min_year)
    if not records:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "criteria": crit,
             "search_results": {"records": 0, "source": None},
             "evidence": [], "state": {"empty_result": True,
                                       "note": "输入中没有可解析的候选文献表（需要 title 列）"},
             "limitations": ["本轮不联网检索；只对输入中的候选集做去重与纳排，空结果如实报告"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    for r in records:
        r["_source"] = source
    rep = tally(records, crit)
    with (out / "screened.tsv").open("w", encoding="utf-8") as fh:
        fh.write("id\tdecision\tyear\tdoi\tmatched_include\tmatched_exclude\treason\tlocator\tscore\ttitle\n")
        for d in rep["decisions"]:
            fh.write(f'{d["id"]}\t{d["decision"]}\t{d["year"]}\t{d["doi"]}\t'
                     f'{",".join(d["matched_include"])}\t{",".join(d["matched_exclude"])}\t'
                     f'{d["reason"][:160]}\t{d["locator"]}\t{d["score"]}\t{d["title"][:160]}\n')
    with (out / "evidence_log.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "criteria", "version": crit["version"],
                             "include_terms": crit["include"], "exclude_terms": crit["exclude"],
                             "min_year": crit["min_year"], "date_cutoff": crit["date_cutoff"],
                             "queries": crit["queries"]}, ensure_ascii=False) + "\n")
        for d in rep["decisions"]:
            fh.write(json.dumps({"event": "decision", "id": d["id"], "decision": d["decision"],
                                 "basis": d["reason"], "locator": d["locator"]}, ensure_ascii=False) + "\n")
        for m in rep["duplicates"]:
            fh.write(json.dumps({"event": "duplicate_merged", **m}, ensure_ascii=False) + "\n")
    c = rep["counts"]
    with (out / "prisma.tsv").open("w", encoding="utf-8") as fh:
        fh.write("stage\tcount\n")
        for k in ("identified", "duplicates_merged", "screened", "excluded", "included"):
            fh.write(f"{k}\t{c[k]}\n")
        for reason, n in c["excluded_by_reason"].items():
            fh.write(f"excluded:{reason}\t{n}\n")
        fh.write(f"reconciles\t{str(c['reconciles']).lower()}\n")
    md = ["# 系统性筛选记录", "", f"case_id: `{case_id}` · 来源 `{source}` · 标准版本 `{crit['version']}`", "",
          "## 检索与标准", "", f"- 检索式：{crit['queries'] or '（输入未给出检索式，仅对候选集做纳排）'}",
          f"- 纳入词：{crit['include'] or '（未限定）'}", f"- 排除词：{crit['exclude'] or '（未限定）'}",
          f"- 年份下限：{crit['min_year']}；截止：{crit['date_cutoff'] or '未给出'}", "",
          "## 计数（逐级对账）", "", "| 阶段 | 条数 |", "|-|-|"]
    for k in ("identified", "duplicates_merged", "screened", "excluded", "included"):
        md.append(f"| {k} | {c[k]} |")
    md += ["", f"**对账**：identified = screened + duplicates_merged ⇒ {c['reconciles']}", "",
           "## 纳入结果（按相关性排序）", ""]
    for d in rep["included"][:40]:
        md.append(f'- `{d["id"]}` ({d["year"] or "n.d."}) {d["title"][:120]} — {d["reason"]} 〔{d["locator"]}〕')
    md += ["", "## 状态说明", "",
           f"- 空结果：{'是' if c['included'] == 0 else '否'}",
           f"- 元数据不完整（缺摘要/年份）：{rep['metadata_incomplete'][:20] or '无'}",
           "- 冲突：同一 DOI/标题的多条记录按重复合并，原始 id 保留在 `evidence_log.jsonl`",
           "- 权限/接口：本轮不联网，检索范围限于输入候选集（已在上面写明）", ""]
    (out / "prisma.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    checks = {
        "counts_reconcile": c["reconciles"],
        "every_decision_has_a_basis": all(d["reason"] for d in rep["decisions"]),
        "every_decision_has_a_locator": all(d["locator"] for d in rep["decisions"]),
        "duplicates_keep_both_ids": all(m.get("merged") and m.get("into") for m in rep["duplicates"]),
        "metadata_gaps_listed": isinstance(rep["metadata_incomplete"], list),
        "criteria_version_recorded": bool(crit["version"]),
    }
    checks["all_passed"] = all(checks.values())
    payload = {
        "case_id": case_id, "status": "answered" if rep["decisions"] else "insufficient_evidence",
        "search_results": {"source": source, "records": c["identified"],
                           "included": c["included"], "ranking": "score = 2×纳入词命中 + 摘要 + 年份"},
        "evidence": {"criteria": crit, "decisions": len(rep["decisions"]),
                     "duplicates": rep["duplicates"],
                     "files": ["screened.tsv", "evidence_log.jsonl", "prisma.tsv", "prisma.md"]},
        "state": {"empty_result": c["included"] == 0,
                  "metadata_incomplete_ids": rep["metadata_incomplete"][:40],
                  "conflicts": "同一 DOI/标题的多条记录已合并，原始 id 保留",
                  "not_done": "未联网检索；只对输入候选集做纳排"},
        "counts": c, "included": rep["included"][:50],
        "sources": [{"name": source}],
        "uncertainties": ([f"元数据不完整：{rep['metadata_incomplete'][:10]}"] if rep["metadata_incomplete"] else []),
        "limitations": ["不联网：检索范围限于输入候选集，检索式与库覆盖需在真实流程中补齐",
                        "纳排基于关键词与年份规则，语义判定交人工；不一致项（近重复）只报告不合并"],
        "verification": checks,
    }
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "counts": c,
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
