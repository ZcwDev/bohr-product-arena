---
name: study-quality-appraisal-ledger
description: 文献研究质量评价的可复跑台账：候选集去重（同 DOI/同标题合并，双 id 留痕）→ 按标准版本逐条纳排（每条带触发词与原文定位）→ PRISMA 计数对账 → 对纳入文献逐篇做八项质量评价（研究设计、随机化、盲法、对照组、样本量、效应量带不确定度、统计结果、局限讨论），每项给出 yes / no / not_reported 与决定该结论的原句，并据此给出 high / moderate / low / unclear 评级与理由。用于文献研究质量评价、质量评价清单、系统综述偏倚评估类任务。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/appraise_ledger.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 文献研究质量评价

## 产出

| 文件 | 内容 |
|-|-|
| `screened.tsv` | 逐条纳排：判定 · 年份 · DOI · 命中纳入/排除词 · 理由 · **定位** · 排序分 |
| `evidence_log.jsonl` | 标准版本与纳入/排除词 → 每条 decision（依据+定位）→ duplicate_merged（双 id） |
| `prisma.tsv` / `prisma.md` | 逐级计数 + 对账（`identified = screened + duplicates`、`screened = included + excluded`） |
| `appraisal.tsv` / `appraisal.md` | **逐篇质量评价**：评级 · 理由 · 满足项 · 未报告项 · 证据原句 |
| `result.json` | `search_results` / `evidence` / `state` / `counts` / `appraisal` / `verification` |

## 八项质量清单（每项三态）

`design_named`（研究设计）· `randomisation`（随机分配）· `blinding`（盲法）· `control_group`（对照组）·
`sample_size`（样本量）· `effect_with_uncertainty`（效应量带 SD/CI）· `statistics_reported`（检验与 p 值）·
`limitations_stated`（局限/偏倚讨论）。

- `yes`：原文命中了该项，并给出**决定该结论的原句**；
- **`no`**：原文明说"未做/没有"（如 "No control group was used"）—— 与 `not_reported` 严格区分；
- `not_reported`：原文没写（不等于没有做）。

评级：≥6 项 yes → `high`；4–5 → `moderate`；2–3 → `low`；<2 或缺摘要 → `unclear`，理由里写明"仅 k/8 项满足"。

## 自查（result.json.verification）
`counts_reconcile`、`every_decision_has_a_basis`、`every_decision_has_a_locator`、`duplicates_keep_both_ids`、
`metadata_gaps_listed`、`criteria_version_recorded`、`every_appraisal_has_evidence_or_not_reported`。

## 失败处理

| 情况 | 行为 |
|-|-|
| 输入没有带 title 的候选表 | `status: insufficient_evidence` + `state.empty_result=true`，不补造文献 |
| 记录文本过短（无摘要） | 评级 `unclear`，理由写"记录文本过短"，不猜 |
| 未联网 | `state.not_done` 写明"检索范围限于输入候选集" |
| 标准未给出 | 用默认并在日志首条写明 |

## 边界
不联网检索；纳排与评价都是规则式（关键词+三态判定），语义判断与双人仲裁交人工；
`not_reported` 与 `no` 的区别贯穿所有输出，避免把"没写"当成"没做"。
