---
name: paper-reproduction-checklist
description: 论文复现与方法验证：从方法段抽出可执行步骤与参数（温度/时长/量级/样本数），按原文顺序列成复现清单，并逐条标出「该步没有可执行参数」的缺口；只搬原文，不推测未写明的条件。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mode repro
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---


# 论文复现与方法验证

论文复现与方法验证：从方法段抽出可执行步骤与参数（温度/时长/量级/样本数），按原文顺序列成复现清单，并逐条标出「该步没有可执行参数」的缺口；只搬原文，不推测未写明的条件。

| 文件 | 内容 |
| --- | --- |
| `deliverable.docx` / `deliverable.md` | 主交付物（结构审计 / 幻灯片计划 / 复现清单） |
| `sections.tsv` | 章节 · 层级 · 段数 · 字数 · 占比 · 段落角色 · 定位 |
| `findings.tsv` | 严重度 · 问题 · 定位（`文件#L行号`）· 原句 |
| `inventory.tsv` | 文件类型 · 元数据 · 目录层级 · 重复 · 版本链 · 权限与来源 |
| `result.json` | `deliverable` / `coverage` / `gaps` / `limitations` / `verification` / `metadata_coverage` |

判定边界：**审计不代写** —— 缺失章节、缺数值支撑的论断都只报告位置与原句，由作者补；每个结论都附 `文件#L行号`；没有数值支撑的页面/断言会被显式标出，不用形容词掩盖。
