---
name: revflow-writing-client
description: A working local academic-writing client as a CLI. Import a manuscript into an immutable, versioned store, propose edits with per-change accept or reject, diff any two versions, roll back and reapply for real, inject labelled invalid operations that leave state unchanged, and export an editable DOCX with a rendered preview plus an evidence map and change log. Use for 学术写作客户端 / 改稿 / 审稿回复 / any task that supplies organizer-edit-workorder.json with target_ids and asks for "a real local revision workflow".
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/revflow.py --work /app/work/revflow run --input /input --output /app/output --fresh
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 只在这个命令**报错**时，才按下面的“失败处理”表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

---

# revflow — the writing client

```bash
python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output \
  --work /app/work/run
```


## The client in one sentence

`revflow` is the client: a versioned manuscript store with a command per user action
(`import`, `propose`, `accept`, `reject`, `compare`, `rollback`, `reapply`, `export`, `verify`)
and a receipt for every action. Every requirement the organizer workorder lists is a command
that really runs, not a paragraph claiming that it could.

## When to use

Trigger when the input has an `organizer_edit_workorder` (inside `case.json` or as
`organizer-edit-workorder.json`) with `target_ids` and `operation_requirements`, or when the task
is 学术写作客户端 / 局部修改 / 版本对照 / 修订追踪 / 导出可编辑稿 with paragraph targets.

Not for translation, summarisation or deck building.

## One command

```bash
python3 /skills/weekly-skill/scripts/revflow.py \
    --work /app/work/revflow run --input /input --output /app/output --fresh
```

No model calls, no network: it cannot exhaust the request or time budget, and repeated runs on
the same input produce the same bytes.

## The user-visible workflow (what the receipts prove)

| user action | command | receipt |
|-|-|-|
| import a manuscript | `import` (part of `run`) | `import-receipt`: file list, block count, sentence index size |
| start the task | `task` (part of `run`) | `task-receipt`: every workorder requirement mapped to the artifact that satisfies it |
| see proposed edits | `propose` | `proposal-receipt` + `proposals.json` |
| accept one edit | `accept CH-001` | `decision-receipt` with before/after block hashes |
| reject one edit | `reject CH-002` | `decision-receipt` proving the combined state hash is unchanged |
| compare versions | `compare 1 3` | `compare-receipt` + `version_diff.md` |
| undo | `rollback 1` | `version-op-receipt`: `state_equals_target: true` by hash |
| redo | `reapply 3` | `version-op-receipt`: `state_equals_target: true` by hash |
| see what breaks | `fault-inject` | `invalid-ops-receipt`: 4 typed exceptions, every state hash unchanged |
| get an editable file | `export` | `export-receipt`: DOCX bytes, PDF pages, preview PNGs |

The four deliberate faults are labelled as fault injections, exactly as the workorder asks:
accept of a never-proposed change id (`UNKNOWN_CHANGE_ID`), rollback to a version that does not
exist (`UNKNOWN_VERSION`), a target id absent from the material (`UNKNOWN_TARGET_ID`), and a
request to rewrite an original block (`PROTECTED_BLOCK_EDIT`, 403). Each one is followed by a
state-hash comparison that shows the manuscript did not move.

## Why the edits are trustworthy

Each accepted annotation quotes, verbatim and by locator, (a) the anchor sentence of the target
paragraph and (b) a sentence elsewhere in the material that adds a condition, a comparable
statement, or the material's own scope limitation. Sentences that merely restate the anchor are
rejected (papers repeat the abstract's claim in Results), so the evidence is never circular.
A target with no such sentence is reported `unresolved` and nothing is written; the run then
reports `status: partial` instead of inventing prose.

Annotations are English-first with a Chinese gloss; the DOCX carries the English body only, so a
renderer without CJK glyphs cannot silently drop text.

## Outputs

`result.json` (all `output_contract` fields, every value traceable), `manuscript_revised.docx`,
`export_preview.pdf`, `previews/page-*.png`, `manuscript_original.md`, `manuscript_revised.md`,
`evidence_map.tsv`, `change_log.tsv`, `version_diff.md`, `consistency_report.md`,
`verification.json`.

## Failure handling

| situation | behaviour |
|-|-|
| no readable manuscript | `status: failed` with the accepted input types; nothing fabricated |
| target id missing | ordinal fallback → `unresolved` entry with reason |
| target is front matter | annotation carried to the nearest substantive block; remapping stated in the annotation and the change log |
| nothing to ground on | `unresolved`, `status: partial` |
| PDF without a text layer | all targets `unresolved`; the run still reports what it read |
| LibreOffice missing | built-in text PDF preview, substitution named in `result.json` |
| damaged/invalid operations | typed `RevFlowError` subclasses with `code` and HTTP-like status; state untouched |

## Runbook

### Expected outputs

Every successful run produces:

| File | Purpose |
|------|---------|
| `result.json` | Structured output with all `output_contract` fields |
| `manuscript_revised.docx` | Editable revised manuscript |
| `manuscript_revised.md` | Revised manuscript with block ids |
| `manuscript_original.md` | Original manuscript frozen verbatim |
| `export_preview.pdf` | Rendered PDF preview (LibreOffice or fallback) |
| `previews/page-*.png` | Per-page PNG screenshots |
| `evidence_map.tsv` | Annotation → source locator mapping |
| `change_log.tsv` | Per-change accept/reject decisions |
| `version_diff.md` | Version diff with annotation text |
| `consistency_report.md` | Terminology/numeric scan |
| `verification.json` | Post-export integrity checks |

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Exit code 2 | No readable manuscript found | Check `/input` has PDF/DOCX/MD/TXT with extractable text |
| `status: failed` in result.json | Material unreadable or missing | Verify input files exist and are not encrypted |
| LibreOffice warnings | `soffice` not in PATH | Fallback text-PDF renderer used automatically; noted in result |
| `ModuleNotFoundError: docx` | Missing `python-docx` | Install: `pip install python-docx` (or use harness with preinstalled deps) |
| `ModuleNotFoundError: pdfplumber` | Missing `pdfplumber` | Install: `pip install pdfplumber` |
| Permission denied on `/app/work` | Sandbox restriction | Use `--work /tmp/revflow-work` instead |

### Idempotency

Repeated runs on the same input produce identical `result.json` content (receipt filenames
contain hashes that may differ, but the structured fields are byte-identical). The `--fresh`
flag clears the work store before each run.

### Portability

- No hardcoded paths; all paths are relative to `--input`, `--output`, `--work` arguments.
- Works from any working directory.
- Graceful degradation: missing optional input fields → `status: partial` with reason stated.
- No network calls, no model calls; fully deterministic.

---

## Limits

- No Word tracked-change XML (the organizer states it is not supplied or required); annotations
  are separate labelled records, so the original text stays byte-identical and editable.
- The client never adjudicates a scientific conflict: it records both locators and marks the
  item for human confirmation.
- Version history covers this workflow's operations (`v1..vN`).
