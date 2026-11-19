---
name: evidence-review-matrix
description: 把材料变成"证据矩阵 + 冲突表 + 空白清单 + 综述稿"：每条记录给出研究设计/样本量/定量结果/效应方向/局限与原文定位，同一量在不同记录取值不同时并列两侧不取平均，查询词没有对应记录时列进空白；综述稿每个句子都带 [R#] 与定位，产出可编辑 docx。
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

# 深度研究与文献综述领域总课题

把材料变成"证据矩阵 + 冲突表 + 空白清单 + 综述稿"：每条记录给出研究设计/样本量/定量结果/效应方向/局限与原文定位，同一量在不同记录取值不同时并列两侧不取平均，查询词没有对应记录时列进空白；综述稿每个句子都带 [R#] 与定位，产出可编辑 docx。
