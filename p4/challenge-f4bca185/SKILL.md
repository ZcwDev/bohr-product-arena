---
name: 文献数据清洗与标准化
description: 把杂乱文献数据整理成可复算的清洗台账：缺失/删失/零值三分、异常只标记不删除、单位换算留因子、日期统一 ISO、规则聚合与文件对账，每步带定位与前后计数。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研数据清洗与处理

规则驱动的表格清洗：原始快照+sha256、缺失（区分未检出与 0，不插补）、离群只标记不删除、只删完全重复、单位换算带因子、日期 ISO 化；规则表按条件聚合、逐格改动留痕，报告计数与写出文件对账。

产出与自查见脚本写出的 `result.json`（含 `status`、证据定位与 `verification`）。
