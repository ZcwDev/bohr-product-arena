---
name: product_arena_04_4b82ad21-section-writer
description: 写作08｜引言与相关工作写作：两段式——脚本先把材料里的证据排成表（每条带定位与数值），再由你的模型按引言与相关工作的真实写作要求成稿（缺口/已有工作分组/与本文差异），最后脚本逐项校验（六节齐全 / 每段有引用或数值 / 所有数字能回到证据表）；只允许写材料支持的内容，缺证据的句子必须删。
---

## 立刻执行（不要探索）

第一步（脚本：生成证据骨架与初稿）：

```bash
python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

第二步（你的模型成稿，这不占用 shell 时间）：读 `/app/output/claim_source_map.tsv`（证据表，每行有内容与定位），
按 **Abstract / Introduction / Methods / Results / Discussion / Conclusion** 六节写成 `/app/output/draft.md`，
每段至少带一个 [n] 引用或一个具体数值；**只写证据表支持的内容**，缺证据就不写。

第三步（脚本校验，不合格会 exit 1）：

```bash
python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output --verify-draft /app/output/draft.md
```

- 校验项：六节齐全（中英标题都认）/ 每段有引用或数值 / 所有数字能在证据表里找到 / 正文长度足够；
- 结果写 `draft_check.json`，缺什么就补什么再跑一次。

---

# 写作08｜引言与相关工作写作

两段式：脚本先把材料里的证据排成表（每条带定位与数值），再由你的模型按引言与相关工作的真实写作要求成稿（缺口/已有工作分组/与本文差异），最后脚本逐项校验；只允许写材料支持的内容，缺证据的句子必须删。

| 文件 | 内容 |
| --- | --- |
| `manuscript.docx` / `deliverable.md` | 脚本生成的初稿（材料句子直接成节，带引用定位） |
| `claim_source_map.tsv` | 证据表：每行 = 一条可用内容 + 数值 + 材料定位 |
| `draft.md` | 你的模型按六节写作要求成稿的版本（更好的一版） |
| `draft_check.json` | 机检：六节齐全 / 每段有引用或数值 / 数字可回到证据表 / 长度 |
| `inventory.tsv` · `repro_manifest.json` · `run_state` | 六维元数据、复跑清单、运行状态口径 |

判定边界：**不编数据、不编文献** —— 证据表里没有的证据不写；校验不过就补写或删句，不要改校验阈值；
数字必须与证据表逐字一致（含单位）。
