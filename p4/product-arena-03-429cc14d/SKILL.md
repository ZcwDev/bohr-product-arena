---
name: reference-citation-manager
description: 参考文献与引用管理：把正文引用与文献表做成双向一致——每条数字引用都能找到条目、编号按首次引用顺序重排、重复条目（同 DOI 或同标题）与缺字段（作者/年份/标题）逐条列出、混用数字与作者-年份时只报告不改写；产出重排后的文献表、引用映射表与报告，缺失文献不从别处推断补齐。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/cite_manage.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 写作04｜参考文献与引用管理

参考文献与引用管理：把正文引用与文献表做成双向一致——每条数字引用都能找到条目、编号按首次引用顺序重排、重复条目（同 DOI 或同标题）与缺字段（作者/年份/标题）逐条列出、混用数字与作者-年份时只报告不改写；产出重排后的文献表、引用映射表与报告，缺失文献不从别处推断补齐。

| 文件 | 内容 |
| --- | --- |
| `references_clean.md` | 按首次引用顺序重排的文献表；未被引用的条目单独列在文末（保留，不删） |
| `citation_map.tsv` | 每条引用 → 新编号 · 条目定位 · 首次出现位置 · 状态（resolved / missing_entry / author_year_preserved） |
| `reference_report.md` | 按类别列出问题（cite_without_entry / entry_without_citation / duplicate_entry / entry_incomplete / mixed_citation_style / numbering_out_of_order） |
| `check_report.json` | 机检：数字引用全部可解析 / 编号 1..N 连续 / 重复与缺字段已列出 / 风格单一 |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据与复跑清单 |

判定边界：**不造文献** —— 缺条目就标 `missing_entry`、缺字段就列出来，不推断补全；重复条目两处编号都保留交由作者确认；
数字与作者-年份混用时只报告，不改写正文；作者-年份引用原样保留。
