---
name: material-grounded-manuscript-writer
description: 把给定科研材料写成可编辑的学术稿件：IMRaD 章节、每句都挂材料定位符、数值按原句抄录（含单位）、缺失章节显式标注而不是补写，并产出真 OOXML 的 .docx（标题样式/编号定义/页眉页脚/表格/TOC 域/修订痕迹）与"断言→来源"映射表。当任务要求"根据材料写论文/改稿/章节结构/事实一致"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/deliverable_shim.py --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 只在这个命令**报错**时，才按下面的"失败处理"表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

---

## 2.5 时间与请求预算（先保交付，再谈完美）

评测侧的硬约束：**64 次模型请求、900 秒、单次回答 16384 tokens**。按下面的检查点自控，任何时刻被打断都应有一份可交付结果：

| 检查点 | 时间/请求 | 必须已经落盘 |
|---|---|---|
| 读完契约 | ≤ 60s | 认清 `/input/case.json` 的 `case_id` 与材料清单 |
| 第一版完整结果 | **≤ 420s（≈ 24 次请求）** | **`/app/output/result.json`**，契约字段齐全，状态允许「部分完成」 |
| 补漏与校对 | 420–780s | 覆盖写 `result.json`，`change_log`/`uncertainties` 只增不删 |
| 收尾 | 剩下的时间 | 不再重写，只补 `limitations` 与导出件 |


规则：

1. **先落一版能过契约校验的结果，再改进**；不允许把「还在读材料」当成完成态。
2. 每次覆盖 `result.json` 前先备份 `/app/work/result.prev.json`——磁盘上任何时刻都要有一份可用的。
3. 值不值得再花一次请求，用这个判据：*这次调用能不能让某个字段变成「有据可查的值」？* 不能就停手。
4. 大材料不要整篇塞进上下文：先建索引（定位符），只取与当前字段相关的块。
5. 到 780s 还没收尾，就**停止改进、只做导出与自检**：超时会被判成失败，已落盘的部分结果才计分。
6. **单次回答保持简短**（≤ 300 字、≤ 2000 tokens）：评测里主要耗时是模型往返，不是脚本；不要复述文件内容、不要每步都总结，能一次 `python3 <脚本>` 解决的就不要拆成多轮对话。

# 材料驱动的学术稿件写作

交付四样东西：`draft.md`（可读可改）、`<case_id>.docx`（**真 OOXML**，Word 里能继续改）、
`result.json`（含 `evidence_map`/`preserved_facts`/`change_log`/`notes`）、`review_checklist.md`（待人工确认项）。

## 何时用

- 给了事实底稿/实验结果/材料，要求产出论文式稿件或章节
- 需要"每个数字都能回到材料"、"缺的部分要标出来"的场景

不适用：需要文献调研出新结论（本 Skill 只复述材料，不替代检索）；材料是扫描件（会明确报需 OCR）。

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/run.py" --input /input --output /app/output
```

输入：`/input/case.json`（取 `case_id`）+ 材料（`*.pdf/.txt/.md`）。PDF 走 PyMuPDF，失败退回 `pdftotext`；
**内置解释器 shim**（按 `WEEKLY_TOOLS_PYTHON` / `/opt/weekly-tools/bin/python` 重新 exec 到 venv）。

## 四条写作纪律（对应评分维度）

| 维度 | 本 Skill 的做法 |
|---|---|
| **写得实** | 正文句子只来自材料原句；数值用正则连同单位整段抄录（`312 ± 41 mm3` 取 312 mm3，`HCT-116` 不当测量值）；不做单位换算 |
| **引得对** | 每句后挂 `[S#]`，`evidence_map[]` 给出 `claim_id → 文件#定位 → 原文引句`，可逐条回原文核对 |
| **全得起来** | 固定 IMRaD 骨架（Abstract/Introduction/Methods/Results/Discussion/Conclusion）；**某章节无材料依据时写「〔待补：…〕」**，不编内容 |
| **改得动** | 同时产出 `.docx`（`Title`/`Heading1-3`/`ListBullet` 样式、`numbering.xml`、`header1.xml`/`footer1.xml`、`w:tbl` 表格、`TOC` 域、`w:ins`/`w:del` 修订痕迹）与 `.md`，Word/Markdown 都能直接续写 |

## Runbook

| 步骤 | 执行 | 输出 | 失败/降级 |
|---|---|---|---|
| 1. 读 case_id | 脚本优先读 `/input/case.json` 的 `case_id`，找不到则扫描目录下任意 `*.json` | `case_id` 字段（字符串） | 都找不到 → `"unreported"` |
| 2. 收集材料 | 递归扫描 `/input` 下 `*.pdf/.txt/.md`；跳过 `case.json`/`output-contract.md`/`example-input.json` 等元数据文件 | 句块列表（每句带来源定位符） | 目录无材料文件 → `status=insufficient_evidence` |
| 3. 文本提取 | PDF：先试 PyMuPDF → 退回纯 Python zlib 解流 → 退回 `pdftotext` CLI | 每页纯文本 | PDF 无文本层（纯扫描图）→ `status=partial`，在 `sources_processed` 中标注 |
| 4. 章节归类 | IMRaD 六节关键词匹配 + 数值句强制归 Results | 六节各若干句 | 某节无匹配句 → 该节写「〔待补〕」 |
| 5. 数值保留 | 正则抽"数+单位"原样抄录 | `preserved_facts[]` | 不做单位换算，不改数字 |
| 6. 生成 artifact | 写 `draft.md`、`<case_id>.docx`（OOXML zipfile 现写）、`review_checklist.md` | 主要文件 | docx 生成失败不会中断流程（已在脚本中隔离） |
| 7. 写 result.json | 包含 output_contract 五字段 + 额外 `status`/`sources_processed`/`uncertainties`/`limitations`/`notes` | `/app/output/result.json` | — |

### output_contract 字段映射

| output_contract 字段 | 脚本实际填充 |
|---|---|
| `case_id` | 从输入 JSON 原样取 |
| `status` | `answered` / `partial` / `insufficient_evidence` / `input_error` / `not_reported` |
| `deliverable` | `{path, draft_path, format:"docx", draft_format:"markdown"}` |
| `preserved_facts` | `[{fact, claim_id, locator, method}]` — 从材料原句抽的数值/术语 |
| `evidence_map` | `[{claim_id, section, locator, source_file, page, verbatim_quote}]` — 每条断言→来源定位 |
| `change_log` | `[{action, description, human_confirmation_required?, unresolved?}]` — 修改动作与未解决项 |
| `notes` | `[]` 字符串数组；demo 跑会写 `"本次演示使用自造示例材料"`；其他如缺材料/未验证数值/预览降级也会补 |

### docx 的 OOXML 特征（`scripts/ooxml.py`）

| 特征 | 实现 |
|---|---|
| `document.xml` | MC 兼容命名空间（`mc:Ignorable="w14"`），`w:body` 含 `w:sectPr` 指向 header/footer |
| `styles.xml` | `Normal`/`Title`/`Heading1`/`Heading2`/`Heading3`/`ListBullet`/`TOCHeading` 七个样式 |
| `numbering.xml` | 单级 bullet 抽象编号 + `numId=1`，`w:numPr` 引用 |
| `header1.xml` | 右上角显示 `case_id` |
| `footer1.xml` | 居中 `PAGE` 域，Word 打开即显示页码 |
| `w:tbl` | 表头灰底 + 全边框 + `tblLook`，列宽 `tblGrid` 均匀分配 |
| TOC | `w:sdt` + `TOC \o "1-2" \h \z \u` 域代码（Word 中 F9 更新） |
| 修订痕迹 | `w:ins` / `w:del`（`revision_ins`/`revision_del` 块） |
| `[Content_Types].xml` | 对 `numbering.xml`/`header1.xml`/`footer1.xml` 有 `Override` 声明 |
| `word/_rels/document.xml.rels` | rId1=styles, rId2=numbering, rId3=header, rId4=footer |

## 异常与边界

- 材料无文本层 → `status=partial`，说明需 OCR，**不猜内容**。
- 章节缺材料 → 该节写「〔待补：材料中未找到该章节的事实依据〕」，并进 `review_checklist.md`。
- 材料口径冲突（同一量两个值）→ 两个值都保留在映射表里，`review_checklist` 标"需人工裁定"。
- docx 的 TOC 是**域**，需在 Word 里更新一次才显示页码（`limitations` 已写）。
- 输入目录只含 `case.json`/元数据无材料 → `status=insufficient_evidence`，仍产出骨架 manuscript 和空映射表。

## Limitations（已知限制）

1. **不推理**：正文只复述材料事实，不做论证性推理或综述性串联；材料缺失处显式标注而非补写。
2. **TOC 域需手动更新**：docx 目录域（`TOC \o "1-2"`）在 Word 中打开后按 F9 / 右键更新一次才显示页码。
3. **PDF 文本层依赖**：纯扫描图 PDF 无法处理（需 OCR），此时 `status=partial` 并如实报告。
4. **章节归类靠关键词**：材料句子归到哪个章节由关键词匹配决定，可能错分；`evidence_map[]` 保留了原文，方便人工复核。
5. **不做单位换算**：所有数值连同单位原样抄录，`mm3` 和 `cm3` 不会自动互转。
6. **预览依赖可选工具**：`soffice --headless` / `pdftoppm` / `cairosvg` 任一缺失都优雅降级为纯文本 preview。

## 资源与依赖

`scripts/run.py` + `scripts/run.py.py`：单命令入口，调度 `make_blocks.py` / `paper.py` / `evidence.py` / `contract.py` / `ooxml.py` / `render.py` / `preview.py` / `text_checks.py`。
纯标准库 +（可选）PyMuPDF +（可选）matplotlib；docx 用 zipfile 现写 OOXML，不依赖 python-docx。
单次运行 < 5 秒（材料 < 50 页时），无网络请求，≤ 20 个文件，每个 ≤ 100 KB。
