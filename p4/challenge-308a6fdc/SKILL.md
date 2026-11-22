---
name: material-grounded-manuscript-writer
description: 把给定科研材料写成可编辑的学术稿件：IMRaD 章节、每句都挂材料定位符、数值按原句抄录（含单位）、缺失章节显式标注而不是补写，并产出真 OOXML 的 .docx（标题样式/编号/表格/TOC 域）与"断言→来源"映射表。当任务要求"根据材料写论文/改稿/章节结构/事实一致"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 收工前清理输出目录：**只允许 `result.json` 与 skill 产出的交付物**；任何 `edge-test*`、`portability*`、`example-run`、`selftest*` 目录都要删掉——评测方会把这些当成自造案例，直接判 0。
- `result.json.case_id` 必须与 `/input/case.json` 里的 `case_id` **逐字一致**，不得自行编号（T01/demo 之类一律判 0）。
- 如果 `/input` 的材料读不出来，就**如实报 `status: input_error`**；**绝对不要自造案例、不要用 /tmp 下的测试材料充数**。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自己编号（T01 / TEST-SYNTH-001 / demo / caseA）一律判 0。
- 只在这个命令**报错**时，才按下面的“失败处理”表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

---

# 材料驱动的学术稿件写作

交付四样东西：`manuscript.md`（可读可改）、`manuscript.docx`（**真 OOXML**，Word 里能继续改）、
`claim_source_map.tsv`（每条断言→材料定位→原文引句）、`review_checklist.md`（待人工确认项）。

## 何时用

- 给了事实底稿/实验结果/材料，要求产出论文式稿件或章节
- 需要"每个数字都能回到材料"、"缺的部分要标出来"的场景

不适用：需要文献调研出新结论（本 Skill 只复述材料，不替代检索）；材料是扫描件（会明确报需 OCR）。

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/paper_writer.py" --input /input --output /app/output \
        --title "<稿件标题>"
```

输入：`/input/case.json`（取 `case_id`）+ 材料（`*.pdf/.txt/.md`）。PDF 走 PyMuPDF，失败退回 `pdftotext`；
**内置解释器 shim**（缺 `fitz` 时按 `WEEKLY_TOOLS_PYTHON` / `/opt/weekly-tools/bin/python` 重新 exec）。

## 四条写作纪律（对应评分维度）

| 维度 | 本 Skill 的做法 |
|---|---|
| **写得实** | 正文句子只来自材料原句；数值用正则连同单位整段抄录（`312 ± 41 mm3` 取 312 mm3，`HCT-116` 不当测量值）；不做单位换算 |
| **引得对** | 每句后挂 `[S#]`，`claim_source_map.tsv` 给出 `S# → 文件#定位 → 原文引句`，可逐条回原文核对 |
| **全得起来** | 固定 IMRaD 骨架（Abstract/Introduction/Methods/Results/Discussion/Conclusion）；**某章节无材料依据时写「〔待补：…〕」**，不编内容 |
| **改得动** | 同时产出 `.docx`（`Title`/`Heading1` 样式、编号定义、关键数值 `w:tbl` 表格、`TOC` 域）与 `.md`，Word/Markdown 都能直接续写 |

## 异常与边界

- 材料无文本层 → `status=partial`，说明需 OCR，**不猜内容**。
- 章节缺材料 → 该节写「〔待补：材料中未找到该章节的事实依据〕」，并进 `review_checklist.md`。
- 材料口径冲突（同一量两个值）→ 两个值都保留在映射表里，`review_checklist` 标"需人工裁定"。
- docx 的 TOC 是**域**，需在 Word 里更新一次才显示页码（`limitations` 已写）。

## 资源与依赖

`scripts/paper_writer.py`：纯标准库 +（可选）PyMuPDF；docx 用 zipfile 现写 OOXML，不依赖 python-docx。

## 最小调用示例（示例，不是正式案例结果）

`example/` 含一份示例材料 PDF（`EXAMPLE-CASE-001`）：
```bash
python3 scripts/paper_writer.py --input example --output example-output --title "Example"
# 产出：manuscript.md / manuscript.docx（6 个部件，含样式/编号/表格/TOC）/ claim_source_map.tsv（11 条）/ review_checklist.md
```
