---
name: webread-bilingual-sheet
description: 把网页/文档读成可核对的双语阅读单：先抽取正文（丢导航/页脚/脚本）→ 结构提纲 + 逐句编号段落 L1..Ln + 重复术语表；再由翻译逐段填入 translate.tsv（原文列不许改），--fill 生成中英对照稿并核对「每段都有译文」，缺的段落如实列为 missing，不用机翻填充。
---

## 立刻执行（不要探索）

单命令 skill（第一步）。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/webread.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

第二步（同一会话内，逐段翻译后回填）：

```bash
python3 /skills/weekly-skill/scripts/webread.py --input /input --output /app/output \
  --fill /app/output/translate.tsv
```

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

# 网页文献阅读与翻译

把网页/文档读成可核对的双语阅读单：先抽取正文（丢导航/页脚/脚本）→ 结构提纲 + 逐句编号段落 L1..Ln + 重复术语表；再由翻译逐段填入 translate.tsv（原文列不许改），`--fill` 生成中英对照稿并核对「每段都有译文」，缺的段落如实列为 missing，不用机翻填充。

| 文件 | 内容 |
| --- | --- |
| `reading.md` | 来源与标题 · 结构提纲 · 原文逐段（`[L1]`…）· 术语表 · 下一步 |
| `segments.tsv` | `id · kind · locator（文件#Lx）· 原文` —— 抽取结果的唯一真相 |
| `translate.tsv` | `id · 原文 · 译文` —— **只填译文列**，其余列不许动 |
| `glossary.tsv` | 重复出现的外文术语 · 次数 · 首见段 · 统一译名（待填） |
| `bilingual.md` / `bilingual.docx` | 第二步产出：`[Lx]` 原文 + 译文逐段对齐 |
| `coverage.tsv` | 每段 translated / missing，含源文与译文字符数 |
| `result.json` | 第一步 `status=needs_translation`；第二步 `status=answered / partial` |

判定边界：**原文逐字保留** —— `--fill` 会校验原文列与 `segments.tsv` 是否一致，被改动的行记入 `source_edited_rows`；没翻译的段落进 `missing` 并写进 `uncertainties`，绝不编造或机翻填空；术语译名以 `glossary.tsv` 为准，保证同词同译。
