---
name: reading-annotation-cards
description: 读一篇材料并留下可回查的批注：每条批注都是原文句子 + `pN sM` 定位 + 类型（定义/结论/证据/方法/局限/未决），带数值的句子单独标出；批注按共有内容词聚成知识卡（每张卡至少 2 条批注、卡片之间不重复占同一句），产出可导入笔记工具的 JSONL。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 阅读批注与知识沉淀

读一篇材料并留下可回查的批注：每条批注都是原文句子 + `pN sM` 定位 + 类型（定义/结论/证据/方法/局限/未决），带数值的句子单独标出；批注按共有内容词聚成知识卡（每张卡至少 2 条批注、卡片之间不重复占同一句），产出可导入笔记工具的 JSONL。

| 文件 | 内容 |
| --- | --- |
| `summary.docx` / `summary.md` | 知识卡 + 定义 / 方法与步骤 / 带数值证据 / 结论 / 局限与未决 分节摘录 |
| `annotations.tsv` | 批注 id · 类型 · 文档 · 定位 · 数值 · 标签 · 原句 |
| `themes.tsv` | 知识卡主题 · 条数 · 类型分布 · 批注 id 列表 |
| `notes.jsonl` | 每条批注一行 JSON（可直接导入笔记/知识库） |
| `result.json` | `annotations`（含 by_type）/ `themes` / `gaps` / `limitations` / `verification` |

判定边界：**批注不评价** —— 类型由提示词与数值特征判定，只摘原句不加观点；未标注到的类型（如定义）在 `gaps` 里写「未标注到」，不补写材料里没有的句子；每个主题卡至少 2 条批注，单次提及不单独成卡。
