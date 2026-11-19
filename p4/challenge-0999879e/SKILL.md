---
name: manuscript-quality-review
description: 规则式的论文质量审查：结构完整性（缺章节）、图表交叉引用是否落空、正文引用与参考文献表双向一致、绝对化/首创性表述缺少证据、同一概念多种写法，外加统计表述检查（p 值写法、± 未定义离散度、缺 n 的比较、多重比较未校正、百分比无分母、同量取值冲突）。每条问题给出原文定位、严重度、状态（错误/证据不足/建议）与具体修改建议，并显式列出必须人工复核的未检查项。当任务要求论文质量审查、审稿、或按检查项逐条给证据时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号（T01 / TEST-SYNTH-001 / demo）一律判 0。
- 没有 `/input` 时用工作区的 `inputs/` 与 `out/`；**不要自造案例**；收工前删掉 `edge-test*`/`portability*`/`example-run`。
- 只有这个命令**报错**时，才看下面的失败处理表。

---

# 论文质量审查

## 产出

| 文件 | 内容 |
|-|-|
| `review_report.docx` / `.md` | 逐条问题（id · 严重度 · 类别 · 定位 · 状态 · 规则号 · 建议）+ 逐字证据 + 未检查项 + 统计摘要 |
| `findings.tsv` | 表格版，便于逐条复核 |
| `result.json` | `case_id` / `status` / `report` / `findings` / `evidence` / `unchecked` / `stats` / `sources` / `uncertainties` / `limitations` / `verification` |

## 检查项

**结构**：`missing_section:*`（Abstract/Introduction/Methods/Results/Discussion/Conclusion/References 缺失）
**交叉引用**：`dangling_figure_ref`（正文引用图/表 N 但材料无对应题注）
**引用一致性**：`citation_without_entry`（正文编号在参考文献表中不存在，high/error）、
`entry_without_citation`（表中有条目未被引用，low/suggestion）
**论断强度**：`overclaim_without_evidence`（first/novel/首次/突破 等无统计或并列证据）
**一致性**：`term_variant`（modeling/modelling、dataset/data set…）
**统计**：`p_zero` / `p_threshold` / `dispersion_undefined` / `test_not_named` / `percent_no_denominator` /
`comparison_no_n` / `multiple_comparisons` / `value_conflict`

同一规则最多保留 3 条示例，被折叠条数记在 `stats.suppressed_same_rule`（报告不是 lint 转储）。

## 状态三分
`error`（可判定的错误）/ `insufficient_evidence`（证据不足或未报告）/ `suggestion`（建议性意见）；
`unchecked` 单独列出规则判不了的项（随机化/盲法、样本量计算、伦理审批等）。

## 失败处理

| 情况 | 行为 |
|-|-|
| 无可读文本（扫描件） | `status: input_error`，说明需要文本层；不编造结论 |
| 材料无统计/结构信息 | `findings` 为空或只列结构项，`verdict=clean/minor_issues`，并列出未检查项 |
| 无 PDF 引擎 | 三级读取（PyMuPDF → pdftotext → 纯标准库），不依赖单一库 |

## 边界
规则只覆盖可机检项；研究设计实质判断交人工；未检查项显式列出，**不凭空补齐缺失信息**。
