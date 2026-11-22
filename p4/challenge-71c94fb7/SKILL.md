---
name: research-material-collection
description: 把资料收集与迁移做成只读可核对的清单：逐文件 sha256、精确/近似重复（5-gram Jaccard ≥0.8 只报告）、按版本链归并、带 file#pN 的检索、六类归目与证据词，迁移前后清单可比对。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode all \
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

# 科研资料库与文件管理

## 产出（严格只读：不移动、不重命名、不删除任何文件）

| 文件 | 内容 |
|-|-|
| `manifest.tsv` | 每个文件：路径 · 扩展名 · 字节 · **sha256** · 文本抽取方式 · 文本字数 · 是否为该内容的规范副本 |
| `duplicates.tsv` | 完全重复（同 sha256）与近重复（5-gram 词片 Jaccard ≥ 0.8），**只报告不合并** |
| `versions.tsv` | 版本链：仅当文件名只差 v1/v2/final/draft/修订 这类版本词时成立，成员与哈希都列出 |
| `operations_log.jsonl` | 操作证据：每次读取（路径/方式/字节/哈希前 16 位）、搜索、分类的计数 |
| `search_results.tsv` | 检索命中：rank · 路径 · 分数 · **locator（文件#段落）** · 片段 |
| `classification.tsv` | 分类：路径 · 类别 · 置信度 · 命中词 · 依据 |
| `result.json` | `assets` / `evidence` / `failures` / `search` / `classification` / `versions` / `verification` |

## 失败与恢复（不把未完成写成完成）
无法提取文本的文件（扫描件、二进制、权限错误）**列入 `failures` 并写明原因**，不当作已处理；
目录里的 `.git`/`__pycache__`/`node_modules` 跳过并留痕；重复文件只报告，是否合并交人工。

## 自查（result.json.verification）
`every_file_has_hash_and_size`、`duplicates_report_basis`、`version_chains_have_members`、
`search_hits_have_locator`、`classification_has_evidence`、`nothing_modified_in_input`。

## 边界
只读；文本抽取三级降级（PyMuPDF → pdftotext → 纯标准库）；近重复只报告；分类是规则式（路径+正文前 4000 字），
低置信度的归 `unclassified` 而不是硬塞一个类。
