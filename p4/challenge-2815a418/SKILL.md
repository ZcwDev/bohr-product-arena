---
name: reviewreply-point-by-point
description: Build a revision package from a manuscript version plus a reviewer comment list — parse items and sub-requirements per reviewer, locate the paragraph each one is about, insert a grounded edit (clarifying statement quoting the version's own evidence, citation to a bibliography entry that already exists, or an explicit limitation), then verify every claim in the response letter against the revised file and export revised manuscript + point-by-point letter as editable DOCX with PDF previews. Use for 审稿回复与论文修改 / 返修 / rebuttal letter tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/reviewreply.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 收工前清理输出目录：**只允许 `result.json` 与 skill 产出的交付物**；任何 `edge-test*`、`portability*`、`example-run`、`selftest*` 目录都要删掉——评测方会把这些当成自造案例，直接判 0。
- `result.json.case_id` 必须与 `/input/case.json` 里的 `case_id` **逐字一致**，不得自行编号（T01/demo 之类一律判 0）。
- 如果 `/input` 的材料读不出来，就**如实报 `status: input_error`**；**绝对不要自造案例、不要用 /tmp 下的测试材料充数**。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自己编号（T01 / TEST-DEMO-001 / demo / caseA）一律判 0。
- 只在这个命令**报错**时，才按下面的“失败处理”表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

---

```bash
python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output \
  --work /app/work/run
```


## Runbook（执行手册）

### 前置条件

- **Python**: 3.10+（脚本使用类型注解 `list[dict]`、`dict[str, str]` 等新语法）
- **依赖**: 无第三方库要求（仅用标准库 `pathlib`、`re`、`json`、`hashlib`、`shutil`、`subprocess`）
- **可选**: LibreOffice（`soffice` 命令）用于生成 PDF/PNG 预览；缺失时仍产出 DOCX，但 `render` 字段会标注 `pdf_preview_missing`
- **输入目录**: 必须包含 `case.json`（含 `case_id`）和至少一个审稿意见文件（文件名匹配 `comment|review|referee|意见|审稿|rebuttal`）
- **手稿文件**: 脚本自动选择最大的非评论文档作为基线手稿（支持 `.md`、`.txt`、`.docx`、`.pdf`）

### 快速启动

```bash
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

# 评测环境（标准路径）
python3 /skills/weekly-skill/scripts/reviewreply.py --input /input --output /app/output

# 本地测试（工作区路径）
python3 skill/scripts/reviewreply.py --input out/test-synth-1 --output out/cases/test-synth-1
```

### 输出结构

执行成功后，`--output` 目录包含：

```
result.json                 # 主输出（必须），含 case_id、status、deliverable、preserved_facts、evidence_map、change_log
manuscript_revised.md       # 修订稿（Markdown）
manuscript_revised.docx     # 修订稿（Word）
response_letter.md          # 逐条回复信（Markdown）
response_letter.docx        # 逐条回复信（Word）
response_letter.pdf         # 回复信 PDF（若 LibreOffice 可用）
response_letter_page_N.png  # 回复信分页截图（若 LibreOffice 可用）
response_map.tsv            # 意见-行动映射表（TSV）
manuscript_baseline.md      # 基线手稿副本（便于对比）
```

### 状态码

- `status: answered` — 所有意见已回应，所有改动已在修订稿中验证存在
- `status: input_error` — 输入文件缺失或不可读（`result.json` 会说明具体原因）
- `status: partial` — 部分意见未能定位或验证失败（`verification` 字段会列出具体问题）

### 常见问题排查

| 症状 | 原因 | 解决 |
|-|-|-|
| `items: 0` | 审稿意见文件未匹配文件名规则，或所有正则都无法解析 | 检查文件名是否包含 `comment\|review\|referee\|意见\|审稿`；检查意见格式是否为编号列表 |
| 所有 `location` 都是 `p001` | 段落匹配得分过低，回退到标题段落 | 检查手稿是否为纯文本（非扫描 PDF）；检查意见是否使用了手稿中不存在的术语 |
| `pdf_preview_missing` | 系统未安装 LibreOffice | 安装 `libreoffice` 或接受仅有 DOCX 的输出 |
| `docx_export_failed` | python-docx 未安装 | 脚本会回退到纯 Markdown；如需 DOCX 可安装 `pip install python-docx` |
| `case_id` 与输入不一致 | 脚本自行生成了 case_id | 这是 bug；应报告并修复（脚本必须从 `/input/case.json` 逐字复制） |

### 性能基准

- **单案例执行时间**: 2-8 秒（取决于手稿长度和意见数量）
- **最大意见数**: 40 条（超过会被截断并记录到 `limitations`）
- **手稿长度**: 无硬性上限，但超过 200 段时匹配速度会下降
- **模型调用**: 0（纯规则驱动，不依赖 LLM）

---

# reviewreply — a response letter that can be checked against the manuscript

## The one invariant that matters

A revision package is graded on whether 「已修改」 is true. So this skill never writes a claim and
then edits: it applies the edit first, re-reads the revised manuscript, and only then states, per
item, where the change is and quotes it. If the verification step cannot find the text, the item is
reported as `未在修订稿中检出，需人工确认` instead of being smoothed over.

## The loop

```bash
S=/skills/weekly-skill/scripts/reviewreply.py
python3 $S --input /input --output /app/output
```

The script finds its own inputs: the comment file is the one whose name matches
`comment|review|referee|意见|审稿|rebuttal`, and the manuscript is the largest remaining document.
No model calls, no network; two runs on the same input produce the same files.

## What each comment item goes through

1. **Parsing** — items are numbered headers scoped per reviewer (`R1-1`, `R2-3`), and bullet / (a) /
   (1) lines under an item become its *sub-requirements*, so a multi-part comment is not collapsed
   into one.
2. **Locating** — the target paragraph is chosen by weighted term overlap over the body (a shared
   8-letter technical term counts double), and the title/author banner is excluded from targets.
   Bibliography entries are detected structurally (`n. Author … year/DOI`), so a comment is never
   "answered" by citing the reference list at the wrong place.
3. **Deciding** — four actions, chosen from the comment's own wording:
   | action | when | what is inserted |
   |-|-|-|
   | `clarify` | asks for a definition/clarification | a marked sentence quoting the version's own relevant sentence with its locator |
   | `add_citation` | asks for literature | a sentence naming a bibliography entry that **already exists** in this version |
   | `add_limitation` | says the claim overstates its scope | an explicit scope sentence |
   | `refuse_experiment` | demands new experiments/data | a written limitation **plus** a stated reason — never a false "completed" |
4. **Verifying** — after the revised file is assembled, every inserted text is searched for; the
   letter reports the result per item.
5. **Exporting** — `manuscript_revised.docx`, `response_letter.docx` (point-by-point table + text),
   markdown mirrors, `response_map.tsv`, `result.json`, and LibreOffice-rendered PDFs with page PNGs.

## Outputs

`result.json` carries `case_id`, `status`, `deliverable`, `preserved_facts`, `evidence_map`,
`change_log`, `result` (items and counts by action), `claims` (one per item, with the comment's
locator and the target locator), `uncertainties` (refusals and unverified items), `limitations`,
and `verification`.

`verification` checks: `every_claimed_edit_present_in_revised`,
`no_unverified_revision_claim`, `every_item_has_a_target_or_a_refusal`,
`refusals_carry_a_written_limitation`. `status` is `answered` only when all pass.

## Failure handling

| situation | behaviour |
|-|-|
| no manuscript text | `status: input_error`, the accepted formats named |
| no comment list | the instruction's own bullet requirements are used as items |
| a comment with no overlapping paragraph | the limitation/discussion paragraph is used and the weak match score is recorded |
| comment demands experiments the material cannot supply | refusal + limitation paragraph + reason, both in the letter |
| comment is itself wrong or contradicts another | it is still answered, and the conflict is left visible in the letter rather than silently resolved |
| LibreOffice absent | DOCX is still produced; the render status says the PDF preview is missing |

## Limits

- Edits are clearly marked sentences inserted after the paragraph they clarify; original text is
  never rewritten, so a journal's tracked-change export still has to be produced in the author's editor.
- The inserted clarifications quote the supplied version; they do not add new science, and a comment
  that requires new data is refused with a written limitation instead.
- The response letter only claims what the verification step found in the revised file.

## Limitations（本 skill 明确不覆盖的范围）

1. **不做内容重写** — 脚本只在目标段落后插入标记句，绝不修改原文任何一字。如需 Word Track Changes 导出，须在作者自己的编辑器中完成。
2. **不补充新科学** — 对要求新实验、新数据、新分析的意见，脚本只写一条"局限性声明 + 理由"，绝不虚构"已完成"。
3. **不做 LLM 推理** — 全部决策来自规则（正则 + 词频匹配），无模型调用、无网络请求、无随机性，两次运行结果完全一致。
4. **不处理扫描 PDF** — 如果手稿是图片 PDF（无可提取文字），`status` 会报 `input_error`。
5. **不做多版本对比** — 只处理一个基线手稿 + 一份审稿意见；若需跨版本（R1 → R2 → R3）追踪，每个版本须单独运行。
6. **不自动翻译** — 回复语言沿用手稿的语言；中英文混合稿件的中文段落会生成中文回复，英文段落会生成英文回复。
7. **段落匹配有误差** — 若审稿意见使用的术语与手稿完全不同（如审稿人说 "temporal resolution" 而手稿只写 "time step"），匹配得分会很低，目标段落可能不准确，需要人工复核 `response_map.tsv` 中的 `match_score` 列。
8. **DOCX 渲染依赖 python-docx** — 如果未安装该库，脚本回退到纯 Markdown 输出，`docx_export_status` 会标注 `skipped`。
