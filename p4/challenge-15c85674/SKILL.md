---
name: literature-grounded-qa
description: 在给定知识库里检索并作答：每条结论都是原文句子 + 文件与段落定位 [S#]；问题拆成子问题逐个判，知识库没有的子问题明确列为「未覆盖、不作答」，不引用近义但不对应的内容，也不凭常识补答。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

## 2.5 时间与请求预算（先保交付，再谈完美）

评测侧的硬约束：**64 次模型请求、900 秒、单次回答 16384 tokens**。按下面的检查点自控，任何时刻被打断都应有一份可交付结果：

| 检查点 | 时间/请求 | 必须已经落盘 |
|---|---|---|
| 读完契约 | ≤ 60s | 认清 `/input/case.json` 的 `case_id` 与材料清单 |
| 第一版完整结果 | **≤ 420s（≈ 24 次请求）** | **`/app/output/result.json`**，契约字段齐全，状态允许「部分完成」 |
| 补漏与校对 | 420–780s | 覆盖写 `result.json`，`change_log`/`uncertainties` 只增不删 |
| 收尾 | 剩下的时间 | 不再重写，只补 `limitations` 与导出件 |


规则：

1. **先落一版能过契约校验的结果，再改进**；不允许把「还在读材料」当成完成态。
2. 每次覆盖 `result.json` 前先备份 `/app/work/result.prev.json`——磁盘上任何时刻都要有一份可用的。
3. 值不值得再花一次请求，用这个判据：*这次调用能不能让某个字段变成「有据可查的值」？* 不能就停手。
4. 大材料不要整篇塞进上下文：先建索引（定位符），只取与当前字段相关的块。
5. 到 780s 还没收尾，就**停止改进、只做导出与自检**：超时会被判成失败，已落盘的部分结果才计分。
6. **单次回答保持简短**（≤ 300 字、≤ 2000 tokens）：评测里主要耗时是模型往返，不是脚本；不要复述文件内容、不要每步都总结，能一次 `python3 <脚本>` 解决的就不要拆成多轮对话。

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
