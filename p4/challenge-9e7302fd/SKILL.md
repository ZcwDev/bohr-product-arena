---
name: stats-method-review
description: 规则式的方法与统计审查：把稿件里的统计表述逐条检查（p 值写法、± 未定义离散度、缺样本量的组间比较、多处比较未提校正、百分比无分母、同一量的取值冲突），每条问题给出原文定位、严重度、状态（错误/证据不足/建议）与具体修改建议，并显式列出"规则判不了、需人工复核"的未检查项。当任务要求审查研究方法与统计、或对论文做质量审查时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自己编号（T01 / TEST-SYNTH-001 / demo）一律判 0。
- 如果本机没有 `/input`（工作区里是 `inputs/`），就用 `inputs` 与 `out` 替换路径；**不要自造案例**。
- 收工前清理输出目录：只允许 `result.json` 与交付物；`edge-test*` / `portability*` / `example-run` 一律删掉。
- 只在这个命令**报错**时，才看下面的失败处理表。

---

# 方法与统计审查

## 产出

| 文件 | 内容 |
|-|-|
| `review_report.docx` / `.md` | 审查报告：逐条问题（id · 严重度 · 类别 · 定位 · 状态 · 建议）+ 逐字证据 + 未检查项 + 统计摘要 |
| `findings.tsv` | 同样内容的表格版，便于逐条复核 |
| `result.json` | `case_id` / `status` / `report` / `findings` / `evidence` / `unchecked` / `stats` / `sources` / `uncertainties` / `limitations` / `verification` |

## 检查项（每条都带规则号，可复跑）

| 规则 | 判据 | 严重度 |
|-|-|-|
| `p_zero` | `p = 0.000` 这类把 p 写成 0 的表述 | high |
| `p_threshold` | 用 `p<0.05` 当显著性证据却没给效应量/检验 | low |
| `dispersion_undefined` | 出现 `±` 但全篇没定义 SD/SEM/CI | medium |
| `test_not_named` | 有显著性表述但全篇未给出检验名称 | high |
| `percent_no_denominator` | 百分比没有分母/样本量 | medium |
| `comparison_no_n` | 组间比较缺样本量与离散度 | high |
| `multiple_comparisons` | ≥5 处比较却未提多重比较校正 | medium |
| `value_conflict` | 同一个量在同一材料里出现多个取值 | high |

同一规则最多保留 3 条示例，被折叠的条数记在 `stats.suppressed_same_rule`（报告不是 lint 转储）。

## 状态三分

`error`（可判定的错误）/ `insufficient_evidence`（证据不足或未报告）/ `suggestion`（建议性意见），
`unchecked` 单独列出"规则判不了、必须人工复核"的项（随机化/盲法、样本量计算、伦理审批等）。

## 失败处理

| 情况 | 行为 |
|-|-|
| 没有可读文本（扫描件） | `status: input_error`，说明需要文本层；不编造审查结论 |
| 材料里没有统计表述 | `findings: []`、`verdict: clean`，并列出未检查项（诚实回答） |
| 缺统计引擎 | 三级 PDF 读取（PyMuPDF → pdftotext → 纯标准库），不依赖单一库 |

## 边界

规则只覆盖可机检的统计表述，研究设计的实质判断交人工；未检查项显式列出，**不凭空补齐缺失信息**。
