---
name: mindmap-grounded-structure
description: Build an editable, source-grounded mind map from a paper — sections as level 1, the section's most informative claims as level 2, quantitative facts as level 3, plus typed relations (structural / depends / contrasts / conflicts) each with a textual basis. Emits FreeMind .mm, OPML, indented markdown, a laid-out SVG and a rendered PNG, with nodes.tsv / relations.tsv / coverage.json for review. Use for 思维导图 / 论文结构梳理 / 综述整理 / 概念关系 tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/mindmap.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/deliverable_shim.py --output /app/output
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

# mindmap — structure you can check, not a picture you have to trust

## What it produces

```
mindmap.mm     FreeMind XML      → editable in XMind / FreeMind / MindNode
mindmap.opml   OPML 2.0          → editable in most outliners
mindmap.md     indented outline  → editable as plain text, diffs cleanly
mindmap.svg    laid-out map      → vector, labels wrapped on word boundaries
mindmap.png    rendered preview  → rasterised from the SVG at 110 dpi
nodes.tsv      id / parent / kind / evidence_type / locator / label / quote
relations.tsv  source / target / relation / basis
coverage.json  block coverage, node kinds, relation counts, depth, edge budget
result.json    output_contract fields + verification
```

One command:

```bash
python3 /skills/weekly-skill/scripts/mindmap.py --input /input --output /app/output
python3 /skills/weekly-skill/scripts/mindmap.py --check /app/output   # verify an export
```

No model calls, no network; two cold runs produce byte-identical `result.json`.

## How the graph is derived (and why it can be defended)

1. **Sections** are detected from heading-shaped blocks (short, capitalised, or matching the usual
   section names) after removing **page furniture**: a line that repeats on three or more pages is a
   running header, not a heading. Without that filter a 19-page paper contributes 18 identical
   "sections" and the top level of the map is noise. Affiliation lines, editor names and figure
   captions are also excluded.
2. **Claims** are the section's most informative sentences, ranked by (has a quantity) + (has a
   finding verb) + (is a method/limitation/gap), five per section, then restored to document order.
3. **Quantities** are attached under the claim that reports them — value plus unit, exactly as
   written, one node each.
4. **Relations** are created only with a textual basis, and each carries that basis in
   `relations.tsv`:
   - `structural` — hierarchy (always present);
   - `depends` — two claims from different sections share ≥4 content terms (the strongest 40 by
     overlap are kept and the number dropped is reported, because a 300-edge map is unreadable);
   - `contrasts` — the target sentence carries a contrast cue (however / whereas / unlike / …) and
     shares terms with the source;
   - `conflicts` — the same quantity is reported with two different values. These are never
     resolved: both locators are kept so a reader can adjudicate.

A node is only created from a sentence that exists in the material, and every node's `quote` is
re-checked as a substring before export. A caption, footer or affiliation line never becomes a node.

## Verification (`result.json.verification`)

`every_node_quote_verbatim`, `every_node_has_locator`, `every_edge_endpoint_exists`,
`every_parent_exists`, `edge_types_are_typed`, `no_orphan_node`,
`hierarchy_has_at_least_two_levels`. `status` is `answered` only when all of them pass.

Every node also records `evidence_type`: `fact` when the sentence reports a value, `summary` when it
states a claim without one. Labels are the material's own wording clipped at a word boundary (with
`…`), not a paraphrase — a reviewer can find the sentence from the label.

## Output contract

`result.json` carries `case_id`, `status`, `result` (counts, kinds, relations, coverage, layout,
artifacts, render status), `claims` (`kind`, `evidence`, `source_id`, `location` for every node),
`uncertainties`, `limitations`, `verification`.

## Failure handling

| situation | behaviour |
|-|-|
| no readable text layer (scanned PDF) | `status: input_error`, accepted formats listed, nothing invented |
| no detectable headings | one synthetic body section, so the map still forms |
| node budget exceeded | `truncated_at_max_nodes: true` and the cap is recorded in `result.json` |
| too many candidate relations | strongest kept, dropped count published in `edge_budget` |
| SVG rasteriser unavailable | `mindmap.svg` is still produced; `render` says the PNG is missing and why |

## Limits

- Text-layer only: figures inside the paper are not interpreted, and a caption that is not a prose
  sentence is deliberately not turned into a node.
- Relations are lexical and cue-driven; they mark where a human should look, not a proven causal
  claim. `conflicts` are reported, never silently reconciled.
- Very long labels are clipped (word boundary + `…`); the full sentence stays in `quote` and
  `nodes.tsv`.
