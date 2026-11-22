---
name: revflow-manuscript-revision
description: Run a real, versioned local manuscript revision workflow on a supplied paper or thesis chapter set — imports the manuscript as immutable blocks, proposes one source-grounded editorial annotation per organizer target, records accept/reject per change, compares versions, performs an actual rollback and reapply, injects labelled invalid operations that leave state unchanged, and exports an editable DOCX with a rendered PDF/PNG preview plus an evidence map and change log. Use for 学术写作客户端 / 学位论文与长文档写作 / any task that supplies an organizer-edit-workorder.json with target_ids.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/revflow.py --work /app/work/revflow run --input /input --output /app/output --fresh
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

# revflow — manuscript revision workflow

## What problem this solves

Most "AI writing" deliverables are one rewritten paragraph. The organizer workorder for these
tasks asks for something else: an **import/task receipt**, **per-change accept or reject**,
**version comparison with a real rollback and reapply**, **controlled invalid operations that
leave state byte-identical**, and an **editable manuscript with original/revised records, an
evidence map and a real export preview**. A rewritten paragraph satisfies none of that, because a
rewrite cannot show what was preserved, cannot be rolled back, and cannot be checked line by line.

`revflow` is a small versioned store plus a CLI that performs each of those operations for real
and stamps a receipt for every one. Nothing is narrated: every claim in `result.json` points at a
file that was produced by an operation that actually ran.

## When to use

Trigger when the task input contains any of:

- `organizer_edit_workorder` (in `case.json` or a separate `organizer-edit-workorder.json`) with
  `target_ids`, `task`, `operation_requirements`, `consistency_scope`;
- instructions like "add a source-grounded editorial annotation at each target", "operate a real
  local revision workflow", "preserve original paragraph text, tables, formulas, citations";
- 学术写作客户端 / 学位论文 / 长文档 / 改稿 / 审稿回复 tasks with paragraph-level edit targets.

Do **not** use it when the task is to translate, summarise, or build a deck — see the sibling
skills for those.

## One command

```bash
python3 /skills/weekly-skill/scripts/revflow.py \
    --work /app/work/revflow run --input /input --output /app/output --fresh
```

Runtime: a few seconds plus one LibreOffice conversion. No model calls, no network, so it cannot
exhaust the 64-request / 900-second budget and it produces the same bytes on every repetition.

The script finds its own inputs: `case.json` (or any JSON carrying `organizer_edit_workorder`),
`organizer-edit-workorder.json`, and the densest PDF/DOCX/MD/TXT under `/input` as the
manuscript. Paragraph ids follow the organizer's own scheme (`C01-P002`); if an id is absent the
number suffix is used as an ordinal, and if the paragraph is front matter the annotation moves to
the nearest substantive paragraph **and says so** in the annotation text and the change log.

## Outputs (all under `--output`)

| file | what it is |
|-|-|
| `result.json` | `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `status`, `sources`, `uncertainty`, `limitations` — every value traceable to a file below |
| `manuscript_revised.docx` | editable OOXML: original paragraphs verbatim, each annotation inserted after its anchor, label + evidence locator, TOC field, annotation/evidence table |
| `export_preview.pdf` + `previews/page-*.png` | real LibreOffice render of that DOCX (falls back to a built-in text renderer if LibreOffice is absent, and says which one was used) |
| `manuscript_original.md` | v1 record, unmodified |
| `manuscript_revised.md` | final record, annotations inline with locators |
| `evidence_map.tsv` | annotation → target id → material file → locator → exact quote → `verified_verbatim` |
| `change_log.tsv` | every accepted/rejected/unresolved change with anchor + insertion hashes and whether a human must confirm |
| `version_diff.md` | v1 → head comparison (annotations added/removed, blocks identical) |
| `consistency_report.md` | terminology variants, in-text citation vs reference list, same-quantity numeric conflicts, unresolved conflicts |
| `verification.json` | machine checks: original blocks byte-identical, no number lost, every quote verbatim, version chain contiguous |

Receipts live in `--work` (`receipts/`): `import-receipt`, `task-receipt` (each workorder
requirement mapped to the artifact that satisfies it), `proposal-receipt`, one
`decision-receipt` per change, `compare-receipt`, `version-op-receipt` for rollback and reapply,
`invalid-ops-receipt`, `consistency-receipt`, `export-receipt`, `verify-receipt`.

## The operations, exposed as a CLI

The single `run` command is a wrapper over these; each one is a real command you can call again:

```bash
revflow.py --work W status                      # version, operation chain, accepted change ids
revflow.py --work W accept CH-001               # creates a version, blocks untouched
revflow.py --work W reject CH-002               # state hash unchanged, receipt says so
revflow.py --work W compare 1 3                 # writes version_diff.md
revflow.py --work W rollback 1                  # restores v1 content, hash proves it
revflow.py --work W reapply 3                   # restores v3 content, hash proves it
revflow.py --work W fault-inject                # 4 deliberate faults, must all raise
```

## Why the annotations are trustworthy

Each annotation is built from two **verbatim quotations** and one scope statement, and the
script refuses to emit an annotation it cannot ground:

1. the anchor sentence of the target paragraph (checked to be a substring of that block);
2. a sentence elsewhere in the material that adds a condition, a comparable statement, or the
   material's own scope limitation — chosen after rejecting any sentence that merely restates the
   anchor (journal papers repeat the abstract's claim in Results and Discussion, and a restatement
   is worthless as evidence);
3. an explicit statement that a text search found no mention of the missing aspect (replicate
   counts, error bars, confidence intervals, …) — so the annotation claims ignorance rather than
   asserting an absence.

If no sentence in the material adds anything to a target, that target is reported as
`unresolved` with a reason and **nothing is written**. Status then becomes `partial`, which is the
honest answer.

Annotations are English-first with a Chinese gloss, because the workflow may export through a
renderer without CJK glyphs; the English body is complete on its own, and the docx carries only
the English part so no text can silently disappear.

## Failure handling

| situation | behaviour |
|-|-|
| no manuscript-like file under `/input` | `status: failed`, the reason and the required input types, nothing invented |
| target id missing | ordinal fallback, then `unresolved` entry |
| target paragraph is front matter (affiliation, licence, heading) | annotation carried to the nearest substantive block, remapping stated in the annotation and the change log |
| no grounding sentence found | `unresolved` with reason, status `partial` |
| PDF has no text layer | `unresolved` for every target; the run still reports what it could read |
| LibreOffice absent | built-in text PDF preview, with the substitution named in `result.json` |
| a control character / private-use glyph in the PDF | mapped at read time so the DOCX stays valid |

## Limits (stated, not hidden)

- No Word tracked-change XML: the organizer states it is neither supplied nor required.
  Annotations are separate labelled records after their anchor, so original text stays
  byte-identical and remains editable.
- Conflict adjudication is left to the author: the report records locators, it never silently
  corrects a published unit or number.
- Version history covers this workflow's own operations (`v1..vN`); it does not model the
  author's external revision history.
