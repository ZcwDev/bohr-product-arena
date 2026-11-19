---
name: systematic-screening-ledger
description: 可复跑的系统性文献纳排台账：给候选集做去重（同 DOI / 同规范化标题合并，两个 id 都留痕）、按明确的标准版本逐条判定纳入或排除（每条都保留触发词与原文定位）、输出可对账的 PRISMA 计数（identified = screened + duplicates，screened = included + excluded），并显式列出空结果、元数据不完整（缺摘要/年份）、合并冲突与"未联网"这类边界。用于系统性文献检索与纳排筛选、范围综述、指南证据更新等任务。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 系统性筛选台账

## 产出

| 文件 | 内容 |
|-|-|
| `screened.tsv` | 逐条结果：id · 判定 · 年份 · DOI · 命中纳入词 · 命中排除词 · 理由 · **定位** · 排序分 · 标题 |
| `evidence_log.jsonl` | 事件流：`criteria`（标准版本/纳入词/排除词/年份下限/检索式）→ 每条 `decision`（含依据与定位）→ `duplicate_merged`（合并的 id 与被合并到的 id） |
| `prisma.tsv` / `prisma.md` | 逐级计数 + **对账**：`identified = screened + duplicates_merged`、`screened = included + excluded` |
| `result.json` | `search_results` / `evidence` / `state`（空结果、元数据不完整、冲突、未完成项）/ `counts` / `verification` |

## 标准从哪里来
优先读 `case.json` 的 `criteria` / `screening_criteria` / `eligibility`（含 `include` / `exclude` / `min_year` /
`date_cutoff` / `version`）；没有就退到默认（不限词、年份下限 1990），**并把"标准版本 + 实际用到的词"写进
`evidence_log.jsonl` 第一条**，这样别人能按同一版标准重跑。

## 判定顺序（每一步都有理由）
1. 同 DOI → 合并；无 DOI 时同"规范化标题"（去标点/大小写/空白）→ 合并（两个 id 都留在日志里）。
2. 命中排除词 → exclude（理由写上命中的词）。
3. 年份早于下限 → exclude。
4. 有纳入词但一条都不命中 → exclude。
5. 元数据不完整（缺摘要或年份）→ `include_with_caveat`，并单独列进 `state.metadata_incomplete_ids`。
6. 其余 → include，按 `2×纳入词命中 + 摘要 + 年份` 排序。

## 自查（result.json.verification）
`counts_reconcile`、`every_decision_has_a_basis`、`every_decision_has_a_locator`、
`duplicates_keep_both_ids`、`metadata_gaps_listed`、`criteria_version_recorded`。

## 失败处理

| 情况 | 行为 |
|-|-|
| 输入没有带 title 的候选表 | `status: insufficient_evidence` + `state.empty_result=true`，说明需要什么格式，**不补造文献** |
| 没有标准 | 用默认并在日志里写明"标准未给出，使用默认" |
| 无摘要/年份 | 标 `metadata_incomplete`，纳入但带 caveat，列出待人工核对 |
| 未联网 | 在 `state.not_done` 与限制里写明"检索范围限于输入候选集" |

## 边界
不联网检索（检索式与库覆盖需在真实流程中补齐）；关键词+年份规则做初筛，语义判定与双人仲裁交人工；
近重复只报告不合并，全部合并都留痕。
