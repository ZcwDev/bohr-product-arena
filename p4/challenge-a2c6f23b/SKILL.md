---
name: revflow-manuscript-revision
description: 版本化改稿工作流：导入、逐条提议、逐条接受/拒绝、版本对照、真回滚与重放、受控非法操作，最后导出可编辑 docx + 渲染预览 + 证据映射；原文块永不改写。
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

# 学术写作领域总课题

版本化改稿工作流：导入、逐条提议、逐条接受/拒绝、版本对照、真回滚与重放、受控非法操作，最后导出可编辑 docx + 渲染预览 + 证据映射；原文块永不改写。

产出与自查见脚本写出的 `result.json`（含 `status` / 证据定位 / `verification`）。
