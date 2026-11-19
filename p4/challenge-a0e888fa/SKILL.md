---
name: rule-driven-table-cleaning
description: 规则驱动的科研表格清洗：先给原始文件做快照与哈希，再按显式规则处理五类问题——缺失（区分"未检出 ND"与 0，不插补）、离群（只标记不删除）、重复（只删完全重复，近似重复报人工）、格式（单位换算带因子、日期统一 ISO 8601）、质量报告（前后计数与规则计数，并与写出的文件对账）。每一次改动都记在 cleaning_log.tsv（原值/新值/规则/原因），规则表 rules.tsv 带触发条件与依据，任何人都能按规则复跑逐格核对。用于科研数据清洗与处理任务。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研数据清洗

## 产出

| 文件 | 内容 |
|-|-|
| `original/<表名>` | **原始快照**（字节原样 + sha256 前 16 位记在 result.json） |
| `<表名>` | 清洗后的表（列名与单位保持原样） |
| `rules.tsv` | 规则表：`rule_id · 文件 · 列 · 触发条件 · 动作 · 计数 · 依据`（按条件聚合，不是逐格清单） |
| `cleaning_log.tsv` | 逐格改动：`行 · 列 · 原值 · 新值 · 规则 · 原因` |
| `quality_report.md` | 前后计数、五类问题汇总、异常与人工确认、复现说明、边界 |
| `result.json` | `case_id` / `status` / `result` / `rules` / `anomalies` / `quality_evidence` / `verification` |

## 五类处理（每条都有规则号）

1. **缺失**：空串 / NA / N/A / -999 / -9999 / `.` → `MISSING`，**不填均值、不删行**；
   `ND`、`<LOD`、`未检测` 等**未检出**值保留为 `ND`，**不当作 0、不插补**（两者语义不同）。
2. **离群**：超出 `Q1-3·IQR .. Q3+3·IQR` 的值**只标记**（写进 cleaning_log 与异常说明），不删除、不替换。
3. **重复**：完全相同的行删掉并保留首次出现；近重复（仅大小写/空白差异）**只报告**，交人工。
4. **格式**：浓度族单位（mg/L、µg/mL、g/L、ng/mL、pg/mL）按因子换算到目标单位并记录 factor；日期统一 ISO 8601，**不丢时分**。
5. **报告**：前后行数、各类计数，并把 `MISSING` 计数与**写出文件重算**对账（`report_matches_file`）。

## 自查（result.json.verification）
`original_snapshot_kept`、`every_rule_has_basis`、`every_change_logged`、`report_matches_file`、
`no_imputation_done`、`anomalies_listed`。

## 失败处理

| 情况 | 行为 |
|-|-|
| 没有可读表格 | `status: input_error`，说明本轮只处理 CSV/TSV |
| 表头无单位 | 单位换算不执行，规则表里记"单位未知，未换算" |
| 编码/分隔符异常 | 按 utf-8 读、失败不断行；异常写进 anomalies |
| 非浓度单位 | 原样保留，不臆造换算 |

## 边界
只处理 CSV/TSV（Excel 二进制与 PDF 表格不做，明确声明）；缺失不插补、离群不删除——这两件都需要领域知识，
交人工确认；单位换算只覆盖浓度族，其余单位原样保留并在报告里说明。
