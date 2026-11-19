---
name: deep-research-agent
description: 深度研究 Agent：把一次深研做成可核对的证据矩阵——每条记录的研究设计/样本量/定量结果/效应方向/局限逐项带定位，跨记录冲突并列不取平均，空白清单单列，综述稿每句带 [R#]。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 深度研究 Agent

深度研究 Agent：把一次深研做成可核对的证据矩阵——每条记录的研究设计/样本量/定量结果/效应方向/局限逐项带定位，跨记录冲突并列不取平均，空白清单单列，综述稿每句带 [R#]。

| 文件 | 内容 |
| --- | --- |
| `evidence_matrix.tsv` | 记录 × 字段（设计/样本量/结果/方向/局限）+ 定位 |
| `conflicts.tsv` / `gaps.tsv` | 跨记录冲突（并列不平均）与研究空白清单 |
| `review.md` / `review.docx` | 综述稿，每句带 [R#] 引用 |
| `inventory.tsv` · `repro_manifest.json` | 六维元数据与复跑清单 |

判定边界：**只从材料里取，不替材料补** —— 每一条结论都带 `文件#定位`；材料没写的字段写「材料未说明」而不是猜；
无法判定的项写进 `gaps`，不写成已完成。
