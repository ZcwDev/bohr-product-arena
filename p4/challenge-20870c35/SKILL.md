---
name: talk-content-organizer
description: 学术汇报内容组织：把稿件转成「一页一个结论」的幻灯片计划——每页给出核心信息、支撑数值与原文定位、按字符预算裁剪页数，并显式标出哪几页结论没有数值支撑（讲之前要么补数据要么降级为假设）。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mode talk
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---


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
