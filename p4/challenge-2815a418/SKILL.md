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
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自己编号（T01 / TEST-SYNTH-001 / demo / caseA）一律判 0。
- 只在这个命令**报错**时，才按下面的“失败处理”表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

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
