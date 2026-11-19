---
name: literature-grounded-qa
description: 在给定知识库里检索并作答：每条结论都是原文句子 + 文件与段落定位 [S#]；问题拆成子问题逐个判，知识库没有的子问题明确列为「未覆盖、不作答」，不引用近义但不对应的内容，也不凭常识补答。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 文献智能问答

在给定知识库里检索并作答：每条结论都是原文句子 + 文件与段落定位 [S#]；问题拆成子问题逐个判，知识库没有的子问题明确列为「未覆盖、不作答」，不引用近义但不对应的内容，也不凭常识补答。

| 文件 | 内容 |
| --- | --- |
| `answer.docx` / `answer.md` | 逐个子问题一条结论，每条带 `[S#]` 与（文件 段落）定位；无法支持的子问题写进「未作答」 |
| `passages.tsv` | 检索证据：每个问题 top-8 段落与 BM25 分数；零命中写 `no passage matched` |
| `citations.tsv` | `[S#]` → 文件 · 段落 · 分数 · 被引用原句 |
| `coverage.tsv` | 子问题 → answered / missing（缺什么一眼看到） |
| `result.json` | `answer` / `sources` / `citations` / `uncertainties` / `verification` |

判定边界：**只抽取不生成** —— 每条结论都是原文句子；相关度要求两条同时满足（BM25 占比 ≥ 0.3 且字面覆盖 ≥ 0.5），不足即判 missing；不引用沾边但不对应的段落，也不用常识补答。
