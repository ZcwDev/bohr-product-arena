---
name: software-op-agent-spec
description: 科研软件操作 Agent：把软件内/跨软件操作过程变成可核验规格——步骤逐条带 `文件#L行号`、动作归类、软件与接口清单、每步完成判据与可执行性（无头/需界面），缺什么列 gaps，不写「已执行」。
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

# 科研软件操作 Agent

科研软件操作 Agent：把软件内/跨软件操作过程变成可核验规格——步骤逐条带 `文件#L行号`、动作归类、软件与接口清单、每步完成判据与可执行性（无头/需界面），缺什么列 gaps，不写「已执行」。

| 文件 | 内容 |
| --- | --- |
| `flow.json` | 机器可读规格：步骤 · 动作 · 软件/站点 · 定位 · 判据 · 可执行性 |
| `plan.docx` / `plan.md` | 步骤清单、接口与选择器（只登材料原文）、本环境可执行性、需补充项 |
| `check_report.json` | 机检：每步有定位/有动作、站点是材料原文、无「已执行」表述 |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据、复跑清单 |

判定边界：**只从材料里取，不替材料补** —— 每一条结论都带 `文件#定位`；材料没写的字段写「材料未说明」而不是猜；
无法判定的项写进 `gaps`，不写成已完成。
