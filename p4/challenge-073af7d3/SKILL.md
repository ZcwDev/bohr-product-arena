---
name: trend-scan-counted
description: 把语料变成可数的趋势证据：逐年产出、主题频次、上升/下降主题（判定式：近期占比比 ≥2，或早期为 0 且近期 ≥2 条记录），单次提及不判趋势，无法定年的记录单独报告而不是丢掉，附纯标准库 SVG 图。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/trend_scan.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 研究前沿与趋势分析

把语料变成可数的趋势证据：逐年产出、主题频次、上升/下降主题（判定式：近期占比比 ≥2，或早期为 0 且近期 ≥2 条记录），单次提及不判趋势，无法定年的记录单独报告而不是丢掉，附纯标准库 SVG 图。

| 文件 | 内容 |
| --- | --- |
| `trend.docx` / `trend.md` | 逐年产出、上升主题（含算术依据）、下降主题、图表、局限 |
| `per_year.tsv` | 年份 · 记录数 · 该年高频词 |
| `terms.tsv` | 主题 · 记录数 · 首末年份 · 近期/早期计数与占比 · 判定 |
| `emerging.tsv` | 上升主题 + 判定算式（占比比或「早期为零」） |
| `trend.svg` | 逐年记录数折线 + top-3 上升主题频次（纯标准库生成，无依赖） |
| `result.json` | `trend` / `emerging` / `declining` / `uncertainties` / `verification` |

判定边界：趋势必须能被数出来 —— 只有 ≥2 条记录的主题参与判定；「早期占比为 0 且近期 ≥2 条」或「占比比 ≥2」才叫上升；同义词不合并，术语变体在 `terms.tsv` 里分列，不靠人工合并冒充结论。
