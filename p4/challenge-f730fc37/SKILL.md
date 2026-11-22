---
name: readclient-anchored-reading
description: A reading client MVP as one CLI. Opens a paper, locates by page/paragraph/keyword, makes a text selection, answers questions from that selection with page-anchored citations, persists annotations, re-resolves every anchor on reopen (including under a different viewer's pagination), and exports a reading record as editable DOCX/PDF with a drift report. Use for 阅读客户端 / 选区问答 / 页码段落定位 / 批注沉淀 / 阅读记录与导出 tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/readclient.py --work /app/work/reading run --input /input --output /app/output
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

# readclient — a reading client whose notes stay on the page

## The failure this is built around

The task names it: a user selects a sentence, asks a question about it, saves the answer, and next
time the paper is opened the note sits on a different paragraph — or has disappeared — because the
page and paragraph numbering changed (another viewer, a re-export, a different extractor). Notes
that carry only a page number are fragile by construction.

`readclient` stores each selection as a **text anchor**: the quoted string, 16 characters of left
and right context, and the quote's SHA-256. The page and block are kept only as hints. On reopen
every anchor is re-resolved against a fresh parse and reported as `exact`, `relocated` or
`missing` — nothing is silently dropped, and drift is data, not a surprise.

## The whole reading loop is one command chain

```bash
S=/skills/weekly-skill/scripts/readclient.py
python3 $S --work /app/work/reading open     --input /input
python3 $S --work /app/work/reading locate   --query "flexural strength"     # or --page 7 / --para C01-P012
python3 $S --work /app/work/reading select   --para C01-P012 --quote "<exact sentence>"
python3 $S --work /app/work/reading ask      --sel SEL-1 --question "under which condition was this measured?"
python3 $S --work /app/work/reading annotate --sel SEL-1 --note "check the 0.2% offset definition"
python3 $S --work /app/work/reading reopen   [--alternate]                   # drift detection
python3 $S --work /app/work/reading export   --output /app/output
```

Or the same loop in one step:

```bash
python3 $S --work /app/work/reading run --input /input --output /app/output
```

No model calls and no network, so the run cannot exhaust a request or time budget, and repeating
it on the same input produces the same bytes.

## What each action produces

| action | stored | receipt |
|-|-|-|
| `open` | `docmodel.json` (blocks with page, block, locator, text hash) | `open-*.json` |
| `locate` | hits with `pid`, page, locator, excerpt | `locate-*.json` |
| `select` | `selections.json` with the anchor (quote + context + sha256) | `select-*.json` |
| `ask` | `annotations.json`: answer + cited passages with locators, or `insufficient_evidence` | `ask-*.json` |
| `annotate` | the human note bound to the same anchor | `annotate-*.json` |
| `reopen` | `drift.json` / `drift-alternate.json`: per-anchor `exact`/`relocated`/`missing` | `reopen-*.json` |
| `export` | `reading_record.md`, `reading_record.docx`, `reading_record.pdf`, `previews/page-*.png`, `result.json` | `export-*.json` |

## Why the answers are checkable

`ask` never composes prose. It scores the selection's own paragraph and the rest of the material
against the question's terms and returns the best matching **sentences, verbatim, with their
locators**; the selection's paragraph is always included as context so the answer cannot drift away
from what was selected. If nothing matches, the status is `insufficient_evidence` and the answer
says so — a gap is reported rather than filled.

## The anchor test that matters

`reopen --alternate` re-parses the document the way a *different viewer* would (blocks merged in
pairs, pages renumbered) and re-resolves every anchor. A page-number-only note cannot survive that;
a text anchor reports `relocated` with the new locator. The export states both counts, so the claim
"notes do not drift" is measured, not asserted.

## Outputs

`result.json` (case_id, status, result, claims with `kind`/`evidence`/`source_id`/`location`,
uncertainties, limitations), `reading_record.docx` (editable, TOC field, annotation anchors,
reopen resolution per note), `reading_record.pdf` + `previews/page-*.png` (real render),
`reading_record.md`, `annotations.json`, `selections.json`, `drift.json`, `drift-alternate.json`,
`receipts/`.

## Failure handling

| situation | behaviour |
|-|-|
| scanned PDF without a text layer | blocks empty → `status: input_error` with the accepted formats; no invented text |
| quote not present in the paragraph | the selection is refused with the paragraph id, store unchanged |
| answer with no matching passage | `insufficient_evidence`, stated in `result.json.uncertainties` |
| anchor text found twice | resolved by context overlap; the choice and candidate count are recorded |
| anchor text gone entirely | `missing` in the drift report, so the reader is told instead of silently losing the note |
| LibreOffice absent | the client's own text PDF fallback, named in `result.json` |

## Limits

- Answers are extractive, not generative: the client returns the passage that supports an answer,
  which is what a reader needs to verify it, but it will not phrase a synthesis.
- No OCR: an image-only PDF is reported.
- The client models one document per workspace; multi-document reading is out of scope.
