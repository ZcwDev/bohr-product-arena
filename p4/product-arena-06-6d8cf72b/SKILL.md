---
name: product-arena-06-6d8cf72b
description: 文献总结与结构化笔记：三段式——脚本抽块→（模型）写六类批注→脚本门禁校验并聚成知识卡；批注六类型（定义/结论/证据/方法/局限/未决）+ 数值标注，导出可导库的 notes.jsonl。
---

## 立刻执行（不要探索）

评测里**直接执行这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input --output /app/output --run-all
```

`--run-all` = 写 `blocks.tsv` → 有 `notes.json` 且过门禁就用它、否则退回规则批注 → 出摘要与 `notes.jsonl`
→ `attach_meta`。三步也可单独跑：`--blocks` / `--verify-notes out/notes.json`。

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

# 阅读10｜文献总结与结构化笔记

文献总结与结构化笔记：批注六类型（定义/结论/证据/方法/局限/未决）+ 数值标注，聚成非重叠知识卡，导出可导库的 notes.jsonl。

## 三段式怎么用（有模型时，批注质量来自这一步）

**第一步 · 脚本抽块** → `/app/output/blocks.tsv`

每行 `locator · chars · text`，`locator` 形如 `文件名#pN`（第 N 段）。脚本只切块、不判断；同一命令已产出一版
规则批注（按提示词 + 数值特征）作底稿。

**第二步 · 模型写 `notes.json`**

```json
{"notes": [{"id": "A1", "note_type": "evidence",
            "quote": "未处理组平均疲劳循环数为 1.2×10^5 次，热处理组为 3.6×10^5 次",
            "locator": "manuscript.md#p9",
            "why": "两组循环数对比，是寿命提升的直接量化证据"}]}
```

| 约束 | 值（第三步逐条校验） |
|---|---|
| `note_type` | 只能是 `definition / claim / evidence / method / limitation / question` |
| `quote` | 在 `blocks.tsv` 里**逐字存在**（忽略空白）；改写过的引文按无证据计 |
| `locator` | **从 `blocks.tsv` 复制**；编造的定位会被拦下 |
| `why` | ≥12 字，说清"这条为什么值得记"（是定义？是量化证据？是作者自述的局限？） |
| 数量 | ≥4 条；六类尽量都有，材料确实没有的类型写进 `gaps`，不要硬凑 |

挑选口径：**一条批注 = 一个可复用的知识点**。定义要挑界定术语的那句；证据要挑带数值/统计结果的对比句；
方法要挑可复现的工艺或参数；局限要挑作者自己承认的边界；未决要挑明确留白的问题。**同一句话不要拆成两条**。

**第三步 · 门禁校验（已包含在 `--run-all` 里）**

`--verify-notes` 写 `notes_check.json`：`quotes_verbatim / locators_from_stage1 / enums_valid / notes_substantive / all_passed`。
不过门禁 → 自动退回规则批注，并在 `result.json` 的 `gaps` 里写明，**不会假装用了模型结果**。

## 交付物

| 文件 | 内容 |
|---|---|
| `summary.docx` / `summary.md` | 知识摘要：主题卡 → 批注（词条带 `文件#定位`） |
| `annotations.tsv` | id · type · doc · locator · numbers · tags · sentence |
| `themes.tsv` | 主题卡 · 条数 · 类型分布 · 批注 id |
| `notes.jsonl` | 每行一条批注，可直接导入笔记库 |
| `notes_check.json` | 模型批注的逐条门禁（是否采用 `notes.json` 的依据） |
| `blocks.tsv` | 第一阶段的块与定位 |
| `inventory.tsv` / `repro_manifest.json` | 六维文件元数据与复跑信息 |

判定边界：**只用材料里有的东西** —— 每条结论带 `文件#定位`；材料没写的字段写「材料未说明」；做不到的写进 `gaps`，不写成已完成。
