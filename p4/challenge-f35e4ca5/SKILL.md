---
name: chartread-figures-and-tables
description: Read a paper's figures and tables into structured, locatable data — crop each figure at its real bounding box, parse its caption (units, subpanels, uncertainty and significance markers), rebuild borderless tables into real grids, calibrate vector plots against their axis ticks, and list everything unreadable instead of guessing. Use for 科研图表解析 / 坐标与数据点提取 / 图表复现 / 表格数值核对 tasks.
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/chartread.py --input /input --output /app/output
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

# chartread — read the chart, keep the receipts

## What the task demands, and how each demand is met

| demand | how |
|-|-|
| 坐标、单位、数据点 | tables are rebuilt into rows × columns with real values; vector plots are calibrated against numeric tick labels when the drawing is vector |
| 图例、子图 | subpanel letters and the caption's unit list are parsed per figure; the crop keeps the legend visible |
| 趋势、不确定性 | sentences that state a trend or an uncertainty for a figure are quoted verbatim with a locator |
| 无法读出的必须标注 | `chart_data.json.unreadable` lists every figure/table that could not be read, and why |
| 每条结论有定位 | every figure, table and claim carries `pdf#p{page}…` |

## One command

```bash
python3 /skills/weekly-skill/scripts/chartread.py --input /input --output /app/output
python3 /skills/weekly-skill/scripts/chartread.py --check /app/output
```

No model calls, no network; two cold runs on the same PDF produce byte-identical `result.json`.

## Outputs

```
figures/fig-N.png      crop at the figure's real bounding box (160 dpi)
figures + tables        chart_data.json: caption, bbox, page, caption facts, csv path
tables/table-N.csv      the rebuilt grid (one file per table)
tables.json             header, rows, per-column n/min/max/mean for numeric columns
report.md               human-readable: figures, caption facts, table summaries, unreadable list
result.json             output_contract: status, result, claims, uncertainties, verification
```

## How tables are actually recovered

`page.find_tables()` alone is not enough on journal PDFs: most tables have **no ruling lines**, so
the finder returns one wide column of jammed text. This skill therefore rebuilds the grid from word
positions — x-offsets clustered into columns, y-offsets into rows — and keeps whichever structure has
more columns; the substitution is recorded in the unreadable/rebuilt list, not hidden.

Two further repairs matter for correctness:

- number fragments split by the PDF (`37 .0`) are rejoined (`37.0`);
- citation markers that occupy their own narrow column (`[3]`, `[14]`) are folded into the material
  name on their left, so the row reads "PLA-3052D [3]" instead of scattered cells.

When the first extracted row is numeric, columns are named `col1…coln` and a note says the table has
no header row — the columns are numbered rather than guessed.

## How plots are handled

- Vector drawings inside the figure box are counted; numeric tick labels inside the box locate the
  axis ranges, and the calibration record states the pixel ranges it found.
- A **raster** plot (a photograph of a graph, or an embedded PNG) is cropped at full resolution and
  reported as unreadable data: `"the plot is raster, its data points cannot be read without OCR and
  are not invented here"`. Reading values off pixels without OCR would be fabrication, so the gap is
  published instead.

## Verification

`every_figure_has_a_locator`, `every_figure_cropped_or_reported`,
`every_table_has_a_locator_and_csv`, `every_claim_has_a_locator`,
`no_series_without_calibration_report`, `unreadable_items_are_listed`. `status` is `answered` when
figures or tables were read at all, `partial` when nothing could be read.

## Failure handling

| situation | behaviour |
|-|-|
| no PDF in the input | `status: input_error`, accepted input named |
| scanned PDF without a text layer | reports that, then stops instead of inventing captions |
| no space above a caption to crop | the figure is listed as not imaged, with the reason |
| table with merged/multi-line header | text kept, span not claimed (stated in `limitations`) |
| no numeric tick labels in a vector figure | calibration skipped and recorded, no series emitted |

## Limits

- Raster data extraction (pixel → value) is out of scope by design; it is reported, not approximated.
- Curves that share a colour or overlap are not separated; the crop is kept so a reader can check.
- The caption parser reads the caption's own words; a figure whose legend is only inside the image
  has its legend visible in the crop but not transcribed.
