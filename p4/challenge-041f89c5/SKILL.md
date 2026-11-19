---
name: topic-novelty-feasibility-eval
description: 把候选选题按可算的三轴评估：可行性（数据/方法/设备证据各 0–3，逐条带原文引文与定位）、新颖性（与材料内最近工作的词面相似度，近即判 incremental 而非吹成首创）、材料对齐度（多少段真的在讨论它）；缺证据的一律写进 gaps，不替选题补合理性，排序即算术结果。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/topic_eval.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研选题与创新性评估

把候选选题按可算的三轴评估：可行性（数据/方法/设备证据各 0–3，逐条带原文引文与定位）、新颖性（与材料内最近工作的词面相似度，近即判 incremental 而非吹成首创）、材料对齐度（多少段真的在讨论它）；缺证据的一律写进 gaps，不替选题补合理性，排序即算术结果。

| 文件 | 内容 |
| --- | --- |
| `eval.docx` / `eval.md` | 每个选题：新颖性标签 + 最近工作与共有词 · 可行性三轴引文 · 材料对齐度 · 缺什么 · 总分 |
| `topics.tsv` | 排名 · 总分 · 三轴分数 · 对齐段数 · 新颖性标签 · 最近文档 · 相似度 · gaps |
| `novelty.tsv` | 选题 · 标签 · 相似度 · 最近文档 · 共有词 · 材料里声明空白/未研究的原句 |
| `feasibility.tsv` | 选题 · 轴 · 分数 · 文档 · 段落 · 引文（无证据写 `no evidence in material`） |
| `result.json` | `topics` / `recommendation` / `gaps` / `limitations` / `verification` |

判定边界：**新颖性只说材料内** —— 相似度 ≥0.6 判 incremental、≥0.3 判 adjacent、否则 gap，不下"国际首创"式结论；可行性只数材料里出现的证据，缺就写 `gaps`；设备/数据是课题组级资源，另用「对齐段数」表示该选题与这些资源的相关程度，不让无关选题蹭资源分。
