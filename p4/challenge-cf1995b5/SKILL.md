---
name: experiment-plan-engine
description: 实验方案（生成与优化）：把课题转成可执行方案——假设、变量与对照、步骤、参数与资源、判读标准逐项带原文定位，参数按原文抄录不换算，风险与异常处置单列，安全/伦理/设备标 needs_confirmation。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 实验方案（生成与优化）

实验方案（生成与优化）：把课题转成可执行方案——假设、变量与对照、步骤、参数与资源、判读标准逐项带原文定位，参数按原文抄录不换算，风险与异常处置单列，安全/伦理/设备标 needs_confirmation。

产出：

| 文件 | 内容 |
| --- | --- |
| `plan.docx` / `plan.md` | 八节方案：假设 → 变量与对照 → 步骤 → 参数（原样抄录）→ 资源 → 判读标准 → 风险与处置 → 需人工确认 |
| `variables.tsv` | 假设 / 自变量 / 因变量 / 对照，每行带 `pN sM` 定位 |
| `parameters.tsv` | 数值 + 单位按原文抄录，**不做换算**，同一句重复出现的同值只记一次 |
| `steps.tsv` | 有序步骤（识别有序列表与"we printed/prepared/measured"式方法句） |
| `risks.tsv` | 风险线索 → 原句 → 处置建议（处置需人工确认，不写成已发生事实） |
| `confirmations.tsv` | 安全 / 伦理 / 设备可用性 / 样本量依据，状态为 `needs_confirmation` 或 `reported` |
| `result.json` | `plan` / `evidence_and_risk` / `confirmations` / `limitations` / `verification` |

判定边界：材料里没有的步骤不补写；找到的每一项都带原文定位；安全、伦理、设备可用性一律列为待确认，**不把未经确认的建议写成可执行事实**。
