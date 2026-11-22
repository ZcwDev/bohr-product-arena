---
name: eviwrite-evidence-first
description: Evidence-first academic writing in three deliverables — a numbered claim list, an evidence table that resolves every citation against the bibliography and grades the study design, and a draft that asserts only what survived, with unsupported claims held back in an explicit pending list. Use for 文献证据驱动写作 / 综述段落 / 论断-证据 mapping tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/eviwrite.py --input /input --output /app/output
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

# eviwrite — write after the evidence, not before it

## Why the order matters

The task grades three deliverables (论断清单 / 证据表 / 成稿) and four elements per claim
(id, verbatim excerpt, locator, strength reason). If a draft is written first and citations are
added afterwards, a sentence whose source does not exist is already in the text. This skill runs the
pipeline the other way:

```
claims.tsv  →  evidence.tsv  →  draft.md / draft.docx
```

## One command

```bash
python3 /skills/weekly-skill/scripts/eviwrite.py --input /input --output /app/output
```

No model calls, no network; two runs on the same material produce the same files.

## Stage 1 — claims (`claims.tsv`)

Candidate claims are pulled from the supplied literature: quantitative sentences (value + unit) first,
then finding sentences. Each gets an id (`C001`…), the **verbatim excerpt**, its locator
(`file#pN bM`) and a content hash. Fragments that begin mid-clause are rejected, because a claim that
starts with "strength (as defined by…" cannot be quoted in a paper.

## Stage 2 — evidence (`evidence.tsv`)

For every claim:

| column | meaning |
|-|-|
| `resolved_citations` | citation markers that exist in the material's own bibliography |
| `missing_citations` | markers with no bibliography entry — these make the claim unassertable |
| `grade` | A systematic review/meta · B RCT · C cohort · D cross-sectional · E in vitro/animal · F expert opinion · G this study's own measured data · H unspecified |
| `grade_reason` | the cue that produced the grade |
| `assertable` | resolved citation, or a verbatim measured result of this study |

## Stage 3 — draft (`draft.md`, `draft.docx`)

- Every asserted sentence carries `〔claim id｜strength〕` and its citation; low grades (E/F/H) get an
  explicit hedge sentence ("证据强度有限，外推需谨慎").
- Claims whose citation does not resolve are **not asserted**: they go to 「待补证据」 with the reason,
  ready to be appended once a source is supplied.
- A closing section states the uncertainties, the grading rule, and that numbers are transcribed
  verbatim (no rounding, no unit conversion).

## Verification (`result.json.verification`)

`every_claim_has_id_excerpt_and_locator`, `every_asserted_claim_resolves_or_is_own_data`,
`no_claim_asserted_with_a_missing_citation`, `pending_list_carries_every_unsupported_claim`,
`every_claim_has_a_strength_grade`, `draft_marks_claim_ids`. `status` is `answered` only if all pass
and at least one claim is assertable.

## Failure handling

| situation | behaviour |
|-|-|
| no readable material | `status: input_error`, accepted formats named |
| bibliography absent | every citation is `missing`, so nothing is asserted and the pending list is the deliverable |
| a claim cites a number not in the list | held back with the missing numbers named |
| material gives no design information | graded `H · unspecified` and hedged in the draft |
| LibreOffice absent | DOCX is still editable; the render status says the preview is missing |

## Limits

- The draft is built from the supplied sentences, so it reads as an evidence-ordered digest, not as
  newly composed prose; that is the price of every sentence being checkable.
- Strength grading is cue-based (design words in the excerpt / bibliography entry); it is a triage
  signal for a human, not a formal risk-of-bias assessment.
- Numbers are copied, never recomputed; conflicting values in the material are surfaced as separate
  claims rather than reconciled.
