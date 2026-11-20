---
name: systematic-review-screened-matrix
description: 系统性文献综述：检索策略→纳排筛选→证据矩阵→冲突与空白→综述稿一条链跑完——检索式（库/字段/时间/语种）与 PRISMA 对账、逐条纳排理由、每条记录的设计/样本量/结果/方向/局限带定位、跨记录冲突并列不取平均、空白单列、综述稿每句带 [R#]。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 系统性文献综述

系统性文献综述：检索策略→纳排筛选→证据矩阵→冲突与空白→综述稿一条链跑完——检索式（库/字段/时间/语种）与 PRISMA 对账、逐条纳排理由、每条记录的设计/样本量/结果/方向/局限带定位、跨记录冲突并列不取平均、空白单列、综述稿每句带 [R#]。

| 文件 | 内容 |
| --- | --- |
| `search_strategy.md` | 检索策略：数据库/字段/时间范围/语种/材料原检索式（逐字）与推导式（标注为推导） |
| `screened.tsv` · `prisma.tsv` / `prisma.md` | 逐条纳排标签与理由；PRISMA 计数与对账（identified = screened + duplicates） |
| `evidence_matrix.tsv` | 记录 × 字段：设计 / 样本量 / 定量结果 / 效应方向 / 局限，每项带定位 |
| `conflicts.tsv` · `gaps.tsv` | 跨记录冲突（并列两侧，不取平均）与空白清单 |
| `review.md` / `review.docx` | 综述稿，每个句子带 [R#] 引用 |
| `evidence_log.jsonl` | 每次判定的原始记录（含被合并的重复条目 id） |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据与复跑清单 |

判定边界：**不补文献、不平均冲突** —— 检索式只认材料原文或由纳入词推导（标注为推导、需人工复核）；
冲突并列两侧并给出方法学原因；空白写清"缺什么"，不写成"没人研究"。
