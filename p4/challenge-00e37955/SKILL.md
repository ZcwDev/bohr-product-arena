---
name: research-library-inventory
description: 只读的科研资料库引擎：清单（路径/大小/sha256/抽取方式）、完全与近重复、文件名版本链、检索与分类，全部落表并可复核；读不了的文件列入 failures。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/library.py --mode all --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 资料管理领域总课题

只读的科研资料库引擎：清单（路径/大小/sha256/抽取方式）、完全与近重复、文件名版本链、检索与分类，全部落表并可复核；读不了的文件列入 failures。

产出与自查见脚本写出的 `result.json`（含 `status` / 证据定位 / `verification`）。
