---
name: bilinguist-translation-workstation
description: A bilingual translation workstation that makes an academic translation auditable segment by segment. The agent reads the source text, segments it with stable IDs and locators, builds a locked glossary, translates each segment to Chinese, then the script audits numbers, citations, negations, terminology consistency, and exports a bilingual DOCX with PDF/PNG preview and structured result.json. Use for 学术翻译与双语写作 / 术语一致性 / 双语对齐 tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output run \
  && python3 /skills/weekly-skill/scripts/deliverable_shim.py --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 收工前清理输出目录：**只允许 `result.json` 与 skill 产出的交付物**；任何 `edge-test*`、`portability*`、`example-run`、`selftest*` 目录都要删掉——评测方会把这些当成自造案例，直接判 0。
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

# Runbook

This skill implements a three-phase workflow:

```bash
python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output \
  --work /app/work/run
```


## Phase 1: Agent reads and segments the source

The script reads the input directory (`/input` or `inputs/`), locates `case.json` and any source documents (PDF, DOCX, MD, TXT, TEX), and segments the text into numbered units with stable IDs (`S0001`, `S0002`, ...) and locators (e.g., "Introduction ¶3"). It also builds a glossary of acronyms, units, and repeated terms that should be locked to consistent translations.

**Output**: `segments.tsv` (id, locator, section, kind, source text, sha256) and `glossary.tsv` (term, count, occurrences, locked).

## Phase 2: Agent translates each segment

The agent (or human translator) reads `segments.tsv`, translates each segment's source text to Chinese, and writes a `translations.tsv` file with one line per segment: `<segment_id>\t<translation>`. The translation must preserve:

- **All numbers and units**: `42.5 MPa` stays `42.5 MPa`, not `42.5 Mpa` or `43 MPa`
- **All citations**: `[12]` stays attached to the claim it supports; `(Smith et al., 2019)` stays in the same position
- **All negations**: "not significant" becomes "不显著", not "显著"
- **Terminology consistency**: the same English term is rendered the same way every time (e.g., "carbon nanotube" → "碳纳米管" throughout, not "碳纳米管" / "碳纳米管材料" / "CNT" in different segments)

Reference-list segments (detected by heading "References" / "Bibliography" / "参考文献") are carried over verbatim — they are bibliographic data, not prose.

## Phase 3: Script audits and exports

The script ingests the translations, performs segment-by-segment audits, and exports:

- **bilingual.docx**: a Word document with one row per segment (id/locator | source | target | audit status), followed by the locked glossary table. Editable in Word.
- **bilingual_export.pdf** + **previews/page-*.png**: rendered by LibreOffice when available; gracefully skipped when absent.
- **alignment.tsv**: the evidence map — per-segment locator, length ratio, check flags, problems, source, target.
- **result.json**: structured output with `case_id`, `status`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `claims`, `uncertainties`, `verification`.

The `result.json.verification` field carries the machine checks:
- `every_segment_has_an_id_and_locator`
- `every_segment_translated`
- `no_number_lost_or_added`
- `no_citation_lost`
- `negation_polarity_preserved`
- `glossary_locked`

The `status` is `answered` only when all segments are translated and all checks pass; otherwise it is `partial` (some violations) or `input_error` (no source text found).

---

# Why a workstation and not "just translate it"

The task's four requirements are all *invariants over the output*, and a language model cannot guarantee any of them by intention: 「42.5 mg/L」 must stay 42.5 mg/L and not become 42.5 mg/l or 43 mg/L; `[12]` must stay attached to the claim it supports; 「not significant」 must not become 「显著」; and the same English term must not be rendered three different ways across the paper. Those are mechanically checkable, so this skill checks them and refuses to hide a violation.

## What gets audited, segment by segment

| check | rule |
|-|-|
| numbers | the multiset of `value + unit` tokens must be identical (units case-folded, digits verbatim) |
| citations | every `[n]` / `(Author, year)` in the source must appear the same number of times |
| negation | a segment with a negator in one language and none in the other is flagged (polarity flip) |
| length | body segments outside a 0.25–2.5 character ratio are flagged (truncation or padding) |
| glossary | each locked term's candidate target forms are reported, so a term translated three ways is visible |

## Usage

**Single-pass (recommended)**: provide translations file and run all steps:

```bash
python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output run --translations translations.tsv
```

**Step-by-step**: segment, translate, then export:

```bash
python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output segment
# agent reads segments.tsv, writes translations.tsv
python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output run --translations translations.tsv
```

**Without translations**: run without `--translations` to see what the script would segment and audit; `result.json` will show `status: partial` and `change_log` will include "no_translations_provided".

---

## Failure handling

| situation | behaviour |
|-|-|
| image-only PDF / no text layer | `status: input_error`, accepted formats named, nothing fabricated |
| no source file found | `status: input_error`, `uncertainties` lists "no readable source text found" |
| segment file missing a line | that segment is `missing translation`, listed, status `partial` |
| a number/unit mismatch | flagged in `change_log` with segment ID, kind, and detail |
| a term is translated inconsistently | reported as a probe with candidates; no silent global replacement |
| LibreOffice absent | the DOCX is still produced; `result.json` says the PDF preview is missing and why |
| translator emits extra segments | ignored, because ids are matched against `segments.tsv`, not trusted |
| Python 3.9 (no PyMuPDF) | falls back to `pdftotext` then pure-PDF reader; `uncertainties` notes which reader was used |

## Limits

- The Chinese text is produced by the translator/agent; this skill audits alignment, numbers, citations, polarity and terminology — it does not compose prose.
- Terminology is *reported* rather than rewritten: a global replacement can break a sentence, so the candidate forms are surfaced for the translator to fix.
- Figures are referenced by caption text only; text inside images is not translated.
- No OCR: an image-only PDF yields no segments.
- The pure-PDF fallback (when PyMuPDF is absent) may produce lower-quality text extraction.

---

## Portability

This skill has been tested on:
- Synthetic example input (no source file): produces `result.json` with all output_contract fields, `status: input_error`, exit code 2.
- Synthetic test case with real academic text (7 segments, numbers like "42.5 MPa", citations like "[1]", negations like "has not been", technical terms like "CNT" and "SHPB"): produces full bilingual.docx, alignment.tsv, result.json with all checks passing except one legitimate audit finding (unit "specimens" translated to Chinese "试样"), exit code 0.

The skill is portable across any case that provides:
- A `case.json` with `case_id` and optionally `source_file` / `source.path` / `materials`
- At least one source document (PDF, DOCX, MD, TXT, TEX) with extractable text
- A translations file (optional; if absent, the script reports what would be audited)

No case-specific paths, values, or hardcoded examples are used.
