---
name: stats-method-review
description: 规则式统计审查：p 值写法、± 未定义离散度、缺 n 的比较、多重比较未校正、百分比无分母、同量取值冲突；每条带定位/严重度/状态/建议。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 统计分析与结果解读

规则式统计审查：p 值写法、± 未定义离散度、缺 n 的比较、多重比较未校正、百分比无分母、同量取值冲突；每条带定位/严重度/状态/建议。

产出与自查见脚本写出的 `result.json`（含 `status`、证据定位与 `verification`）。
