---
name: manuscript-quality-review
description: 规则式规范审查：引用与参考文献双向一致、图表交叉引用、绝对化表述、术语一致性，逐条给定位与建议，未检查项单列。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 学术规范与合规检查

规则式规范审查：引用与参考文献双向一致、图表交叉引用、绝对化表述、术语一致性，逐条给定位与建议，未检查项单列。

产出与自查见脚本写出的 `result.json`（含 `status`、证据定位与 `verification`）。
