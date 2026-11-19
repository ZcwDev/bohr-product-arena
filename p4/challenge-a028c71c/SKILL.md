---
name: readclient-anchored-reading
description: 阅读客户端：打开→定位→选区→问答→批注→重开→导出；每条答案附原文定位，批注存为文本锚（引文+上下文+sha256），重开时逐条判定 exact/relocated/missing。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/readclient.py --work /app/output/work/reading run --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 文献阅读领域总课题

阅读客户端：打开→定位→选区→问答→批注→重开→导出；每条答案附原文定位，批注存为文本锚（引文+上下文+sha256），重开时逐条判定 exact/relocated/missing。

产出与自查见脚本写出的 `result.json`（含 `status` / 证据定位 / `verification`）。
