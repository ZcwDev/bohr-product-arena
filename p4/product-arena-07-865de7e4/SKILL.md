---
name: product_arena_07_865de7e4
description: LaTeX 写作与编译：把稿件的结构与改动做成版本化流程（不可改写块、提议/接受、版本对照、回滚重放），并检查数学与交叉引用标记的完整性。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/revflow.py --work /app/output/work/revflow run --fresh --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 写作13｜LaTeX 写作与编译

LaTeX 写作与编译：把稿件的结构与改动做成版本化流程（不可改写块、提议/接受、版本对照、回滚重放），并检查数学与交叉引用标记的完整性。

产物见下方表格（`result.json` 里 `files` 字段逐项列出）；六维文件元数据在 `inventory.tsv`，复跑信息在 `repro_manifest.json`。

判定边界：**只用材料里有的东西** —— 每条结论带 `文件#定位`；材料没写的字段写「材料未说明」；做不到的写进 `gaps`，不写成已完成。
