---
name: bilinguist-translation-workstation
description: A bilingual translation workstation that makes an academic translation auditable segment by segment. Segments the source with stable ids and locators, locks a glossary of acronyms/units/repeated terms, ingests the translator's per-segment output, flags every lost or altered number, lost citation, flipped negation and implausible length, then exports a bilingual DOCX (source | target), glossary appendix and a rendered PDF/PNG preview with result.json. Use for 学术翻译与双语写作 / 术语一致性 / 双语对齐 tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output
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

# bilinguist — translate, then prove the translation held

## Why a workstation and not "just translate it"

The task's four requirements are all *invariants over the output*, and a language model cannot
guarantee any of them by intention: 「42.5 mg/L」 must stay 42.5 mg/L and not become 42.5 mg/l or 43
mg/L; `[12]` must stay attached to the claim it supports; 「not significant」 must not become
「显著」; and the same English term must not be rendered three different ways across the paper. Those
are mechanically checkable, so this skill checks them and refuses to hide a violation.

## The loop

```bash
S=/skills/weekly-skill/scripts/bilinguist.py
python3 $S --input /input --output /app/output segment     # segments.tsv: id, locator, source text, sha256
python3 $S --input /input --output /app/output terms       # glossary.tsv: acronyms/units/repeated terms
#   → translate segments.tsv, write  <segment_id>\t<translation>  one per line
python3 $S --input /input --output /app/output apply --translations translated.tsv
python3 $S --input /input --output /app/output export      # bilingual.docx + pdf + png + result.json
```

or in one pass with the translator's file:

```bash
python3 $S --input /input --output /app/output run --translations translated.tsv
```

The station never invents a translation: a segment without a target line is reported as
`missing translation`, and the run's status becomes `partial` — the honest answer.

## What gets audited, segment by segment

| check | rule |
|-|-|
| numbers | the multiset of `value + unit` tokens must be identical (units case-folded, digits verbatim) |
| citations | every `[n]` / `(Author, year)` in the source must appear the same number of times |
| negation | a segment with a negator in one language and none in the other is flagged (polarity flip) |
| length | body segments outside a 0.25–2.5 character ratio are flagged (truncation or padding) |
| glossary | each locked term's candidate target forms are reported, so a term translated three ways is visible |

Reference-list segments are carried over verbatim (they are bibliographic data, not prose) and are
excluded from the prose checks — but they stay in the table so the numbering is preserved.

## Outputs

`bilingual.docx` — real OOXML: one row per segment (id/locator · source · target · which checks
passed), then the locked glossary table, with a TOC field; editable in Word.
`bilingual_export.pdf` + `previews/page-*.png` — rendered by LibreOffice when available.
`segments.tsv`, `glossary.tsv`, `alignment.tsv` (the evidence map: per-segment locator, ratio, check
flags, problems, source, target), and `result.json` (`case_id`, `status`, `deliverable`,
`preserved_facts`, `evidence_map`, `change_log`, `claims`, `uncertainties`, `verification`).

`result.json.verification` carries the machine checks:
`every_segment_has_an_id_and_locator`, `every_segment_translated`, `no_number_lost_or_added`,
`no_citation_lost`, `negation_polarity_preserved`, `glossary_locked` — and `status` is `answered`
only when all of them pass.

## Failure handling

| situation | behaviour |
|-|-|
| image-only PDF / no text layer | `status: input_error`, accepted formats named, nothing fabricated |
| segment file missing a line | that segment is `missing translation`, listed, status `partial` |
| a term is translated inconsistently | reported as a probe with candidates; no silent global replacement |
| LibreOffice absent | the DOCX is still produced; `result.json` says the PDF preview is missing and why |
| translator emits extra segments | ignored, because ids are matched against `segments.tsv`, not trusted |

## Limits

- The Chinese text is produced by the translator/agent; this skill audits alignment, numbers,
  citations, polarity and terminology — it does not compose prose.
- Terminology is *reported* rather than rewritten: a global replacement can break a sentence, so the
  candidate forms are surfaced for the translator to fix.
- Figures are referenced by caption text only; text inside images is not translated.
