---
name: reference-citation-manager
description: 参考文献与引用管理：把正文引用与文献表做成双向一致——每条数字引用都能找到条目、编号按首次引用顺序重排、重复条目（同 DOI 或同标题）与缺字段（作者/年份/标题）逐条列出、混用数字与作者-年份时只报告不改写；产出重排后的文献表、引用映射表与报告，缺失文献不从别处推断补齐。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/cite_manage.py --input /input --output /app/output \
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

# 写作04｜参考文献与引用管理

参考文献与引用管理：把正文引用与文献表做成双向一致——每条数字引用都能找到条目、编号按首次引用顺序重排、重复条目（同 DOI 或同标题）与缺字段（作者/年份/标题）逐条列出、混用数字与作者-年份时只报告不改写；产出重排后的文献表、引用映射表与报告，缺失文献不从别处推断补齐。

| 文件 | 内容 |
| --- | --- |
| `references_clean.md` | 按首次引用顺序重排的文献表；未被引用的条目单独列在文末（保留，不删） |
| `citation_map.tsv` | 每条引用 → 新编号 · 条目定位 · 首次出现位置 · 状态（resolved / missing_entry / author_year_preserved） |
| `reference_report.md` | 按类别列出问题（cite_without_entry / entry_without_citation / duplicate_entry / entry_incomplete / mixed_citation_style / numbering_out_of_order） |
| `check_report.json` | 机检：数字引用全部可解析 / 编号 1..N 连续 / 重复与缺字段已列出 / 风格单一 |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据与复跑清单 |

判定边界：**不造文献** —— 缺条目就标 `missing_entry`、缺字段就列出来，不推断补全；重复条目两处编号都保留交由作者确认；
数字与作者-年份混用时只报告，不改写正文；作者-年份引用原样保留。
