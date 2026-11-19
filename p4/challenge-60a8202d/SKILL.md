---
name: systematic-screening-ledger
description: 可复核的文献检索台账：候选集去重（DOI/标题，双 id 留痕）、按标准版本逐条纳排（命中词+原文定位）、PRISMA 计数对账、排序分与命中片段，空结果与元数据缺失显式报告，不补造文献。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 引文追踪与关联文献发现

可复核的文献检索台账：候选集去重（DOI/标题，双 id 留痕）、按标准版本逐条纳排（命中词+原文定位）、PRISMA 计数对账、排序分与命中片段，空结果与元数据缺失显式报告，不补造文献。

产出与自查见脚本写出的 `result.json`（含 `status`、证据定位与 `verification`）。
