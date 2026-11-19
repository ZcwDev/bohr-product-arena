---
name: browser-agent-benchmark-spec
description: 浏览器与软件智能体 Benchmark：把受控操作过程变成可核验规格与评测脚本骨架——步骤带定位、动作归类、站点/选择器只取材料原文、每步完成判据，并给出可复跑的检查项而非声称已执行。
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

# 浏览器与软件智能体 Benchmark

浏览器与软件智能体 Benchmark：把受控操作过程变成可核验规格与评测脚本骨架——步骤带定位、动作归类、站点/选择器只取材料原文、每步完成判据，并给出可复跑的检查项而非声称已执行。

| 文件 | 内容 |
| --- | --- |
| `flow.json` | 操作规格：步骤 · 动作 · 站点 · 判据 · 可执行性（browser / http-api） |
| `plan.docx` / `plan.md` | 步骤、来源定位、材料给出的选择器与接口、需补充项 |
| `check_report.json` | 机检：定位完整、站点字面、选择器仅登记、无「已执行」 |
| `inventory.tsv` · `repro_manifest.json` | 六维元数据与复跑清单 |

判定边界：**只从材料里取，不替材料补** —— 每一条结论都带 `文件#定位`；材料没写的字段写「材料未说明」而不是猜；
无法判定的项写进 `gaps`，不写成已完成。
