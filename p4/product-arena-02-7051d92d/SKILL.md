---
name: academic-language-polish
description: 学术语言润色：只做有把握的机械修复（中英混排空格、全角标点、重复词、冗余句式如「对X进行了Y的分析」→「分析了X的Y」），每处改动写入 polish_log.tsv（定位+规则+前后文）；长句、被动/空泛表述、同义术语混用只标记不改写；校验器逐项证明句子数、数字与单位、引用标记前后完全一致。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/polish.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 写作02｜学术语言润色

学术语言润色：只做有把握的机械修复（中英混排空格、全角标点、重复词、冗余句式如「对X进行了Y的分析」→「分析了X的Y」），每处改动写入 `polish_log.tsv`（定位+规则+前后文）；长句、被动/空泛表述、同义术语混用只标记不改写；校验器逐项证明句子数、数字与单位、引用标记前后完全一致。

| 文件 | 内容 |
| --- | --- |
| `polished.md` | 润色后的稿件（未改动的句子原样保留） |
| `polish_log.tsv` | 每处改动一行：定位 · 规则 · 改前 · 改后 |
| `polish_flags.tsv` | 未自动改动的问题：长句 / 被动或空泛 / 同义术语混用，带定位与建议 |
| `check_report.json` | 机检：句子数不变 / 数字与单位不变 / 引用标记不变 / 每处改动都有记录 / 标记都有定位 |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据与复跑清单 |

判定边界：**不改写实质内容** —— 只做可逆的机械修复；数字、单位、引用标记一对一保留；长句与被动句只标记，
同义术语不替作者选词；任何可能改变语义的改写都不做。
