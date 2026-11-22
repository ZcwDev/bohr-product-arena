---
name: talk-content-organizer
description: 学术汇报内容组织：把稿件转成「一页一个结论」的幻灯片计划——每页给出核心信息、支撑数值与原文定位、按字符预算裁剪页数，并显式标出哪几页结论没有数值支撑（讲之前要么补数据要么降级为假设）。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mode talk \
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

# 学术汇报内容组织

学术汇报内容组织：把稿件转成「一页一个结论」的幻灯片计划——每页给出核心信息、支撑数值与原文定位、按字符预算裁剪页数，并显式标出哪几页结论没有数值支撑（讲之前要么补数据要么降级为假设）。

| 文件 | 内容 |
| --- | --- |
| `deliverable.docx` / `deliverable.md` | 主交付物（结构审计 / 幻灯片计划 / 复现清单） |
| `sections.tsv` | 章节 · 层级 · 段数 · 字数 · 占比 · 段落角色 · 定位 |
| `findings.tsv` | 严重度 · 问题 · 定位（`文件#L行号`）· 原句 |
| `inventory.tsv` | 文件类型 · 元数据 · 目录层级 · 重复 · 版本链 · 权限与来源 |
| `result.json` | `deliverable` / `coverage` / `gaps` / `limitations` / `verification` / `metadata_coverage` |

判定边界：**审计不代写** —— 缺失章节、缺数值支撑的论断都只报告位置与原句，由作者补；每个结论都附 `文件#L行号`；没有数值支撑的页面/断言会被显式标出，不用形容词掩盖。
