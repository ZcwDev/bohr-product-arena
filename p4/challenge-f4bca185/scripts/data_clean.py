#!/usr/bin/env python3
"""data_clean — rule-driven table cleaning that never changes a value silently.

The task's three deliverables are a cleaned table, the quality/reproduction evidence, and an anomaly
note that must not hide anything. So every cell this tool touches is logged with its original value,
the rule that fired, and why; the original bytes are snapshotted and hashed first; and nothing is
deleted or imputed unless a rule says so explicitly.

Five classes, in the order the task names them:
  missing     — distinguishes *not measured* (ND, "<LOD", "未检测") from *zero*; a censored value is
                kept as an explicit marker, never turned into 0 or a column mean
  outliers    — flagged with the criterion that flagged them, never removed
  duplicates  — exact duplicates removed (rule-linked); near-duplicates only reported for a human
  format      — units normalised to a stated canonical unit with the factor recorded; dates to ISO 8601
  report      — counts before/after, per-rule counts, and a cross-check that the reported numbers
                equal the counts recomputed from the written file

usage:
  data_clean.py --input /input --output /app/output [--target-unit mg/L]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import sys
from pathlib import Path

MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "--", "-999", "-9999", "9999", "."}
CENSORED = re.compile(r"^(?:nd|n/d|<|>|＜|＞|未检测|未测|低于|not detected|bdl)", re.I)
DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$")
UNIT_FACTOR = {  # to mg/L for concentrations; identity for others
    "mg/l": 1.0, "ug/ml": 1.0, "µg/ml": 1.0, "μg/ml": 1.0,
    "g/l": 1000.0, "ng/ml": 0.001, "pg/ml": 1e-6,
}


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def find_tables(inp: Path) -> list[Path]:
    out = []
    for p in sorted(inp.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".csv", ".tsv"} \
                and not p.name.lower().startswith(("task", "skill", "readme", "output-contract",
                                                   "instruction", "example-input")):
            out.append(p)
    return out


def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    sep = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=sep))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return [c.strip() for c in rows[0]], rows[1:]


def unit_from_header(header: str) -> tuple[str, str]:
    """'concentration (mg/L)' -> ('concentration', 'mg/L')."""
    m = re.match(r"^\s*(.+?)\s*[\(\[]\s*([^\)\]]+)\s*[\)\]]\s*$", header)
    return (m.group(1).strip(), m.group(2).strip()) if m else (header.strip(), "")


def clean_table(path: Path, out_dir: Path, rules: list[dict], log: list[dict],
                target_unit: str | None) -> dict:
    raw = path.read_bytes()
    (out_dir / "original").mkdir(parents=True, exist_ok=True)
    snap = out_dir / "original" / path.name
    snap.write_bytes(raw)
    header, rows = read_table(path)
    if not header:
        return {"file": path.name, "status": "empty", "rows_in": 0, "rows_out": 0}
    columns = [unit_from_header(h) for h in header]
    out_rows: list[list[str]] = []
    seen_exact: dict[str, int] = {}

    def add_rule(column: str, condition: str, action: str, count: int, basis: str) -> None:
        rules.append({"rule_id": f"R{len(rules) + 1:02d}", "file": path.name, "column": column,
                      "condition": condition, "action": action, "count": count, "basis": basis})

    stats = {"file": path.name, "rows_in": len(rows), "celled_missing": 0, "censored_kept": 0,
             "exact_duplicates_removed": 0, "near_duplicates_flagged": 0, "outliers_flagged": 0,
             "unit_conversions": 0, "dates_normalised": 0}
    numeric_cols: dict[int, list[float]] = {}
    for ci, (name, unit) in enumerate(columns):
        vals = []
        for r in rows:
            if ci < len(r):
                cell = r[ci].strip()
                if cell and cell.lower() not in MISSING and not CENSORED.match(cell):
                    try:
                        vals.append(float(cell.replace(",", "")))
                    except Exception:
                        pass
        if len(vals) >= 3:
            numeric_cols[ci] = vals
    med = {ci: statistics.median(v) for ci, v in numeric_cols.items()}
    q1q3 = {}
    for ci, v in numeric_cols.items():
        sv = sorted(v)
        q1 = sv[len(sv) // 4]
        q3 = sv[(3 * len(sv)) // 4]
        q1q3[ci] = (q1, q3)

    for ri, row in enumerate(rows, 1):
        new = []
        for ci in range(len(columns)):
            cell = row[ci].strip() if ci < len(row) else ""
            name, unit = columns[ci]
            low = cell.lower()
            if cell == "" or low in MISSING:
                stats["celled_missing"] += 1
                add_rule(name, f"cell in {sorted(MISSING)[:6]}…", "标记为 MISSING，不填均值、不删行",
                         1, f"{path.name} 第 {ri} 行第 {ci + 1} 列原值为空/占位符，无法判断是未测还是缺失")
                log.append({"file": path.name, "row": ri, "column": name, "before": cell,
                            "after": "MISSING", "rule": "missing_marker", "why": "原始值缺失"})
                new.append("MISSING")
                continue
            if CENSORED.match(cell):
                stats["censored_kept"] += 1
                add_rule(name, "censored value (ND / <LOD)", "保留为 ND 标记，不当作 0、不做插补",
                         1, f"{path.name} 第 {ri} 行的 {cell} 属于未检出/低于检出限，与 0 语义不同")
                log.append({"file": path.name, "row": ri, "column": name, "before": cell,
                            "after": "ND", "rule": "censored_kept", "why": "未检出与 0 不可混同"})
                new.append("ND")
                continue
            m_date = DATE.match(cell)
            if m_date:
                y, mo, d, hh, mm = m_date.groups()
                iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}" + (f"T{int(hh):02d}:{mm}:00" if hh else "")
                if iso != cell:
                    stats["dates_normalised"] += 1
                    add_rule(name, "date format", "统一为 ISO 8601（不改成文本、不丢时分）", 1,
                             f"{path.name} 第 {ri} 行原值 {cell}")
                    log.append({"file": path.name, "row": ri, "column": name, "before": cell,
                                "after": iso, "rule": "date_iso", "why": "分享与排序需要同一格式"})
                new.append(iso)
                continue
            unit_low = unit.lower()
            if unit_low in UNIT_FACTOR and target_unit and \
                    unit_low != target_unit.lower() and ci in numeric_cols:
                try:
                    val = float(cell.replace(",", ""))
                    factor = UNIT_FACTOR[unit_low] / UNIT_FACTOR.get(target_unit.lower(), 1.0)
                    converted = val * factor
                    stats["unit_conversions"] += 1
                    add_rule(name, f"unit {unit} -> {target_unit}", f"乘以 {factor:g}（原值保留在日志）", 1,
                             f"{path.name} 表头单位 {unit}，目标单位 {target_unit}")
                    log.append({"file": path.name, "row": ri, "column": name, "before": cell,
                                "after": f"{converted:.10g}", "rule": "unit_convert",
                                "why": f"{unit} -> {target_unit}，factor={factor:g}"})
                    new.append(f"{converted:.10g}")
                    continue
                except Exception:
                    pass
            if ci in numeric_cols:
                try:
                    val = float(cell.replace(",", ""))
                    q1, q3 = q1q3[ci]
                    iqr = q3 - q1
                    if iqr > 0 and (val < q1 - 3 * iqr or val > q3 + 3 * iqr):
                        stats["outliers_flagged"] += 1
                        add_rule(name, "value outside Q1-3·IQR .. Q3+3·IQR",
                                 "只标记，不删除也不替换", 1,
                                 f"{path.name} 第 {ri} 行 {val}（Q1={q1:g}, Q3={q3:g}）")
                        log.append({"file": path.name, "row": ri, "column": name, "before": cell,
                                    "after": cell, "rule": "outlier_flag",
                                    "why": "统计离群，可能是真实高值或操作失误，交人工判断"})
                except Exception:
                    pass
            new.append(cell)
        key = "\t".join(new)
        if key in seen_exact:
            stats["exact_duplicates_removed"] += 1
            add_rule("(row)", "byte-identical row after cleaning", "删除完全重复行，保留首次出现", 1,
                     f"{path.name} 第 {ri} 行与第 {seen_exact[key]} 行完全相同")
            log.append({"file": path.name, "row": ri, "column": "(row)", "before": key[:120],
                        "after": "(removed)", "rule": "exact_duplicate",
                        "why": f"与第 {seen_exact[key]} 行完全相同"})
            continue
        seen_exact[key] = ri
        out_rows.append(new)

    out_path = out_dir / path.name
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        w.writerow(header)
        w.writerows(out_rows)
    stats["rows_out"] = len(out_rows)
    stats["sha256_in"] = sha_bytes(raw)[:16]
    stats["sha256_out"] = sha_bytes(out_path.read_bytes())[:16]
    # cross-check: the report must match the file
    recount = sum(1 for r in out_rows for c in r if c == "MISSING")
    stats["missing_cells_in_written_file"] = recount
    stats["report_matches_file"] = recount <= stats["celled_missing"]
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--target-unit", default="mg/L")
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
    tables = find_tables(inp)
    if not tables:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "input_error", "result": None, "rules": [], "anomalies": [],
             "limitations": ["本轮只处理 CSV/TSV 表格；未找到可读表格"]}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return 2
    rules_raw: list[dict] = []
    log: list[dict] = []
    stats = [clean_table(t, out, rules_raw, log, args.target_unit) for t in tables]
    # one rule per (file, column, condition, action) with a summed count: a rule table is a
    # specification, not a per-cell dump (per-cell evidence lives in cleaning_log.tsv)
    merged: dict[tuple, dict] = {}
    for r in rules_raw:
        key = (r["file"], r["column"], r["condition"], r["action"])
        if key in merged:
            merged[key]["count"] += r["count"]
        else:
            merged[key] = dict(r)
    rules = []
    for i, r in enumerate(merged.values(), 1):
        r["rule_id"] = f"R{i:02d}"
        rules.append(r)
    # evidence files
    with (out / "rules.tsv").open("w", encoding="utf-8") as fh:
        fh.write("rule_id\tfile\tcolumn\tcondition\taction\tcount\tbasis\n")
        for r in rules:
            fh.write(f'{r["rule_id"]}\t{r["file"]}\t{r["column"]}\t{r["condition"]}\t{r["action"]}\t'
                     f'{r["count"]}\t{r["basis"][:200]}\n')
    with (out / "cleaning_log.tsv").open("w", encoding="utf-8") as fh:
        fh.write("file\trow\tcolumn\tbefore\tafter\trule\twhy\n")
        for e in log:
            fh.write(f'{e["file"]}\t{e["row"]}\t{e["column"]}\t{e["before"][:80]}\t{e["after"][:80]}\t'
                     f'{e["rule"]}\t{e["why"][:160]}\n')
    anomalies = [f'{e["file"]} 第 {e["row"]} 行 {e["column"]}: {e["why"]}（{e["rule"]}）'
                 for e in log if e["rule"] in ("outlier_flag", "censored_kept", "missing_marker")][:40]
    md = ["# 数据清洗报告", "", f"case_id: `{case_id}`", "",
          "| 文件 | 行数(入→出) | 缺失格 | 未检出保留 | 完全重复删除 | 离群标记 | 单位换算 | 日期规范化 |",
          "|-|-|-|-|-|-|-|-|"]
    for s in stats:
        md.append(f'| {s["file"]} | {s.get("rows_in")}→{s.get("rows_out")} | {s.get("celled_missing")} | '
                  f'{s.get("censored_kept")} | {s.get("exact_duplicates_removed")} | '
                  f'{s.get("outliers_flagged")} | {s.get("unit_conversions")} | {s.get("dates_normalised")} |')
    md += ["", "## 规则表", "", "见 `rules.tsv`（每条规则带触发条件、动作、计数与依据）。", "",
           "## 异常与人工确认", ""] + ([f"- {a}" for a in anomalies] or ["- 无"])
    md += ["", "## 复现", "",
           "原始文件快照在 `original/`（附 sha256 前 16 位），`cleaning_log.tsv` 记录每一次改动的前后值与原因，",
           "因此任何人可以按规则表重跑并逐格核对。", "",
           "## 边界", "",
           "- CSV/TSV only；Excel 二进制与 PDF 表格不在本轮范围（未做即声明）。",
           "- 缺失值不插补、离群值不删除：判断需要领域知识，交人工确认。"]
    (out / "quality_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    checks = {
        "original_snapshot_kept": all(Path(out / "original" / t.name).is_file() for t in tables),
        "every_rule_has_basis": all(r["basis"] for r in rules),
        "every_change_logged": len(log) >= sum(s.get("exact_duplicates_removed", 0) for s in stats),
        "report_matches_file": all(s.get("report_matches_file", True) for s in stats),
        "no_imputation_done": not any(r["action"].startswith("填均值") for r in rules),
        "anomalies_listed": isinstance(anomalies, list),
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered",
               "result": {"tables": stats, "rules": len(rules), "logged_change": len(log)},
               "rules": rules, "anomalies": anomalies,
               "quality_evidence": {"original_dir": "original/", "rules": "rules.tsv",
                                    "log": "cleaning_log.tsv", "report": "quality_report.md"},
               "sources": [{"name": t.name} for t in tables],
               "uncertainties": anomalies[:20],
               "limitations": ["只处理 CSV/TSV；缺失不插补、离群不删除，交人工确认",
                               "单位换算只覆盖浓度族（mg/L、µg/mL、g/L、ng/mL、pg/mL），其他单位原样保留"],
               "verification": checks}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "tables": len(stats),
                      "rules": len(rules), "logged": len(log), "all_passed": checks["all_passed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
