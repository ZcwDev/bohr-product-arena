---
name: research-library-inventory
description: 只读的科研资料库管理：给整个目录建清单（路径/大小/sha256/文本抽取方式），识别完全重复与近重复（只报告不合并）、按文件名版本词还原版本链，并留下逐次读取的操作日志；无法读文本的文件列入 failures。用于资料库与文件管理、版本关系梳理类任务。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode all
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研资料库与文件管理

## 产出（严格只读：不移动、不重命名、不删除任何文件）

| 文件 | 内容 |
|-|-|
| `manifest.tsv` | 每个文件：路径 · 扩展名 · 字节 · **sha256** · 文本抽取方式 · 文本字数 · 是否为该内容的规范副本 |
| `duplicates.tsv` | 完全重复（同 sha256）与近重复（5-gram 词片 Jaccard ≥ 0.8），**只报告不合并** |
| `versions.tsv` | 版本链：仅当文件名只差 v1/v2/final/draft/修订 这类版本词时成立，成员与哈希都列出 |
| `operations_log.jsonl` | 操作证据：每次读取（路径/方式/字节/哈希前 16 位）、搜索、分类的计数 |
| `search_results.tsv` | 检索命中：rank · 路径 · 分数 · **locator（文件#段落）** · 片段 |
| `classification.tsv` | 分类：路径 · 类别 · 置信度 · 命中词 · 依据 |
| `result.json` | `assets` / `evidence` / `failures` / `search` / `classification` / `versions` / `verification` |

## 失败与恢复（不把未完成写成完成）
无法提取文本的文件（扫描件、二进制、权限错误）**列入 `failures` 并写明原因**，不当作已处理；
目录里的 `.git`/`__pycache__`/`node_modules` 跳过并留痕；重复文件只报告，是否合并交人工。

## 自查（result.json.verification）
`every_file_has_hash_and_size`、`duplicates_report_basis`、`version_chains_have_members`、
`search_hits_have_locator`、`classification_has_evidence`、`nothing_modified_in_input`。

## 边界
只读；文本抽取三级降级（PyMuPDF → pdftotext → 纯标准库）；近重复只报告；分类是规则式（路径+正文前 4000 字），
低置信度的归 `unclassified` 而不是硬塞一个类。
