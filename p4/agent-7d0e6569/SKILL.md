---
name: search-agent-iteration
description: 文献检索 Agent：把检索→去重→纳排→排序做成可复跑流水线，同时交付检索策略（库/字段/时间范围/语种/检索式与纳排对应）、PRISMA 计数与逐条判定依据，供下一轮迭代复用。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 文献检索 Agent 与自动迭代

文献检索 Agent：把检索→去重→纳排→排序做成可复跑流水线，同时交付检索策略（库/字段/时间范围/语种/检索式与纳排对应）、PRISMA 计数与逐条判定依据，供下一轮迭代复用。

| 文件 | 内容 |
| --- | --- |
| `screened.tsv` | 逐条标签（include/exclude/maybe）+ 理由 + 定位 |
| `search_strategy.md` | 检索策略：库/字段/时间范围/语种/原检索式（逐字）与推导式 |
| `prisma.tsv` / `prisma.md` | 计数与对账（identified = screened + duplicates） |
| `evidence_log.jsonl` | 每条判定一行，含合并的原始 id |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据、复跑清单 |

判定边界：**只从材料里取，不替材料补** —— 每一条结论都带 `文件#定位`；材料没写的字段写「材料未说明」而不是猜；
无法判定的项写进 `gaps`，不写成已完成。
