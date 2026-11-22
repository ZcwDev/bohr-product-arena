---
name: ppt_6ad93034-grounded-deck
description: 学术：三段式——脚本先把材料抽成"可引用要点 + 关键数值（带定位）"，再由你的模型写 slide 规格（标题 + 2–6 条要点，每条带 [n] 或 · 定位），最后脚本渲染 pptx/pdf/逐页预览并校验（标题长度 / 要点条数 / 单条长度 / 数值必须能在材料里找到）。所有要点可回到材料，不编内容。
---

## 立刻执行（不要探索）

第一步（脚本：抽取可引用要点与关键数值）：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --title "汇报标题" \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

第二步（你的模型写 slide 规格）：读 `speaker_notes.md` 里的要点与数值（每条都有定位），
写成 `/app/output/slides.json`：每页 `{"title": "...", "bullets": ["... · 文件#段号", "... [n]"]}`，
2–6 条要点/页、单条 ≤160 字符，**每个数值都必须来自材料**。

第三步（脚本按你的规格渲染 + 校验）：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --slides /app/output/slides.json
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --verify-slides /app/output/slides.json
```

- 校验写 `slides_check.json`：标题长度 / 每页要点数 / 单条长度 / **数值可回到材料**；不合格 exit 1，改完再跑。

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

# 学术

三段式：脚本抽要点与数值 → 模型写 slide 规格 → 脚本渲染并校验。所有要点可回到材料，不编内容。

| 文件 | 内容 |
| --- | --- |
| `deck.pptx` / `deck.pdf` | 可编辑演示文稿与渲染件（无 python-pptx 时走纯标准库 OOXML） |
| `previews/page-*.png` | 逐页预览图（有光栅化器时） |
| `speaker_notes.md` | 每页要点的材料定位，便于逐条核对 |
| `slides.json` | 你的模型写的 slide 规格（更好的一版） |
| `slides_check.json` | 机检：标题/要点数/单条长度/数值可回溯 |
| `result.json` · `inventory.tsv` · `repro_manifest.json` | 结果、六维元数据、复跑清单、运行状态 |

判定边界：**不编数字、不编出处** —— 数值必须能在材料里找到（校验会逐条比对）；缺证据的判断不进正文页；
校验不过就改规格，不要改校验阈值。
