---
name: paper-to-talk-deck
description: 把论文做成可编辑 deck：每页一条带定位的论断，含关键数值表与来源页；无 python-pptx 时用纯标准库 OOXML 写出 pptx，并尽力渲染 PDF/PNG 预览。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 学术 PPT 领域总课题

把论文做成可编辑 deck：每页一条带定位的论断，含关键数值表与来源页；无 python-pptx 时用纯标准库 OOXML 写出 pptx，并尽力渲染 PDF/PNG 预览。

产出与自查见脚本写出的 `result.json`（含 `status` / 证据定位 / `verification`）。
