---
name: product-arena-05-64faa4a2-section-writer
description: 写作09｜方法与实验章节写作：两段式——脚本先把材料里的证据排成表（每条带定位与数值），再由你的模型按方法与实验的真实写作要求成稿（材料/仪器/参数（原样）/统计/可重复性），最后脚本逐项校验（六节齐全 / 每段有引用或数值 / 所有数字能回到证据表）；只允许写材料支持的内容，缺证据的句子必须删。
---

## 立刻执行（不要探索）

第一步（脚本：生成证据骨架与初稿）：

```bash
python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

第二步（你的模型成稿，这不占用 shell 时间）：读 `/app/output/claim_source_map.tsv`（证据表，每行有内容与定位），
按 **Abstract / Introduction / Methods / Results / Discussion / Conclusion** 六节写成 `/app/output/draft.md`，
每段至少带一个 [n] 引用或一个具体数值；**只写证据表支持的内容**，缺证据就不写。

第三步（脚本校验，不合格会 exit 1）：

```bash
python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output --verify-draft /app/output/draft.md
```

- 校验项：六节齐全（中英标题都认）/ 每段有引用或数值 / 所有数字能在证据表里找到 / 正文长度足够；
- 结果写 `draft_check.json`，缺什么就补什么再跑一次。

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

# 写作09｜方法与实验章节写作

两段式：脚本先把材料里的证据排成表（每条带定位与数值），再由你的模型按方法与实验的真实写作要求成稿（材料/仪器/参数（原样）/统计/可重复性），最后脚本逐项校验；只允许写材料支持的内容，缺证据的句子必须删。

| 文件 | 内容 |
| --- | --- |
| `manuscript.docx` / `deliverable.md` | 脚本生成的初稿（材料句子直接成节，带引用定位） |
| `claim_source_map.tsv` | 证据表：每行 = 一条可用内容 + 数值 + 材料定位 |
| `draft.md` | 你的模型按六节写作要求成稿的版本（更好的一版） |
| `draft_check.json` | 机检：六节齐全 / 每段有引用或数值 / 数字可回到证据表 / 长度 |
| `inventory.tsv` · `repro_manifest.json` · `run_state` | 六维元数据、复跑清单、运行状态口径 |

判定边界：**不编数据、不编文献** —— 证据表里没有的证据不写；校验不过就补写或删句，不要改校验阈值；
数字必须与证据表逐字一致（含单位）。
