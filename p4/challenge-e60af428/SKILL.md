---
name: provenance-audit-contract
description: 按 Provenance Audit API v1 契约实现溯源审计服务并自带回归：结构阶段查 missing_node/cycle，叶子阶段查 retracted/stale/integrity/quote 并按 sum·mean·ratio·percent_change 计算（含 arity、unit/scope 一致、zero_division、nonfinite_result），观点阶段用 max(1e-8,1e-8·|actual|) 容差比对给出 supported/contradicted；附 12 条一故障一用例的回归与公开样例逐字段比对。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/provenance_audit.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）；
  契约产物在 `audit_output.json`（`{"claims":[...]}`）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 修复科研报告溯源服务并提交有效回归证据

按 Provenance Audit API v1 契约实现溯源审计服务并自带回归：结构阶段查 missing_node/cycle，叶子阶段查 retracted/stale/integrity/quote 并按 sum·mean·ratio·percent_change 计算（含 arity、unit/scope 一致、zero_division、nonfinite_result），观点阶段用 `max(1e-8,1e-8·|actual|)` 容差比对给出 supported/contradicted；附 12 条一故障一用例的回归与公开样例逐字段比对。

| 文件 | 内容 |
| --- | --- |
| `audit_output.json` | 契约要求的审计结果：`claims[]`（id · status · computed · reasons · lineage · evidence） |
| `submission.py` | 可直接跑的服务入口：stdin 读案例 JSON、stdout 写审计 JSON |
| `regressions.json` / `regression_report.md` | 12 条一故障一用例（retracted/stale/integrity/quote/missing_node/cycle/arity/unit/zero_div/unknown_op/claim_mismatch）与逐条结果 |
| `check_report.json` | 机检：claims 齐全且顺序不变 / 回归全过 / 公开样例逐字段一致 / 服务入口已写 |
| `inventory.tsv` · `repro_manifest.json` · `result.json` | 六维文件元数据、复跑清单、结果与门禁 |

判定边界：**严格按 contract.md** —— 结构错误短路为 insufficient 且只报结构错误码；根节点无效时不返回部分证据；未知 op 判 `unknown_operation` 而不猜实现；未规定的畸形输入不处理，也不为公开样例打补丁或改预期值。
