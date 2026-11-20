---
name: research-software-automation-spec
description: 科研软件插件与自动化总课题：把软件内/跨软件的操作流程变成可核验规格——步骤与动作、软件与站点清单、材料给出的接口或选择器、每步完成判据与可执行性（无头/需界面）；缺什么列 gaps，不把未经确认的操作写成已执行事实。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研软件插件与自动化总课题

科研软件插件与自动化总课题：把软件内/跨软件的操作流程变成可核验规格——步骤与动作、软件与站点清单、材料给出的接口或选择器、每步完成判据与可执行性（无头/需界面）；缺什么列 gaps，不把未经确认的操作写成已执行事实。

| 文件 | 内容 |
| --- | --- |
| `flow.json` | 机器可读规格：步骤 · 动作 · 站点 · 定位符 · 完成判据 · 可执行性 |
| `plan.docx` / `plan.md` | 步骤清单、站点与接口、材料里出现过的选择器、本环境可执行性、需补充项 |
| `check_report.json` | 机检闸门：每步有定位/有动作、站点是材料原文、选择器仅在材料引用、无「已执行」表述 |
| `inventory.tsv` | 文件类型 · 元数据 · 目录层级 · 重复 · 版本链 · 权限与来源 |
| `result.json` | `workflow` / `gaps` / `limitations` / `verification` / `metadata_coverage` |

判定边界：**只出规格，不谎报执行** —— 每条步骤都强制带 `文件#L行号` 定位；站点与选择器只登记材料里字面出现的，没出现就写进 `gaps`；标 `browser` 的步骤明确说明本沙箱不代为执行，绝不写「已点击/已抓取」。
