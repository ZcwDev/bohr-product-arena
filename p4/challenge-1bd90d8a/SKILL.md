---
name: weekly-challenge-1bd90d8a
description: 当任务属于「科研数据图表生成与编辑」这一类——数据图必须与源数据逐点对齐：柱高/点位置/色阶/误差线来自数据表，轴标签与单位正确，样式符合目标模板，导出可再编辑。…——需要按案例契约产出可核验结果时使用；提供可执行流程与机检器，所有结论回到材料定位符。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要先做仓库审计：

> **交付状态红线**：顶层 `status` 只用「成功 / 部分完成」；`无法判断`/`unable`/`insufficient_evidence`
> 仅在**完全没有可读输入**时才用（会被判 0）。只要读到材料，就必须交一份带缺口说明的「部分完成」。

```bash
python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。
- 想再改图：编辑 `diagram.json` 后重跑
  `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output`（或
  `figure.py diagram diagram.json -o figured.svg`）。**节点标签必须能在材料里原文找到**，否则换名或删掉。

---

# 科研数据图表生成与编辑｜每周技能（weekly-skill）

本 Skill 面向「科研数据图表生成与编辑」这一类任务：当任务属于「科研数据图表生成与编辑」这一类——数据图必须与源数据逐点对齐：柱高/点位置/色阶/误差线来自数据表，轴标签与单位正确，样式符合目标模板，导出可再编辑。…——需要按案例契约产出可核验结果时使用；提供可执行流程与机检器，所有结论回到材料定位符。

它交付的是**可执行的流程 + 可机检的检查器**，不是一段提示词。所有判断都能回到材料里的定位符，
所有检查都能用命令复跑。


## 0. 第一步永远是读案例契约（不要跳过）

```bash
# 本 Skill 在评测环境里的安装位置（找不到就全局搜一下）
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
  SKILL_DIR=$(dirname "$(find / -name SKILL.md -not -path '*/node_modules/*' 2>/dev/null | grep -i week | head -1)")
fi
echo "SKILL_DIR=$SKILL_DIR"; ls "$SKILL_DIR"
ls -la /input/ 2>/dev/null; ls -la /app/work 2>/dev/null
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/input/case.json")
print(p.read_text(encoding="utf-8")[:4000] if p.is_file() else "no /input/case.json")
PY
```

- `case_id` 必须**逐字**复制到结果里；缺失就直接报 `input_error`，不要猜。
- 案例契约里出现的字段名、枚举值（如 `answered|partial|insufficient_evidence|input_error`）**优先于**本 Skill 自己的措辞。
- 材料路径以实际 `ls` 结果为准；题面提到的文件名可能不存在。

只要上面任何一步对不上，**先按题面修正契约再干活**。


## 1. 本 Skill 做什么

数据图必须与源数据逐点对齐：柱高/点位置/色阶/误差线来自数据表，轴标签与单位正确，样式符合目标模板，导出可再编辑。


## 2. 输出契约（artifact 家族）

`/app/output/result.json`（同时镜像一份到 `/app/outputs/result.json`）：

| 字段 | 要求 |
|---|---|
| `case_id` | 原样保留案例给的 id |
| `status` | `ok` / `partial` / `unable` / `input_error` |
| `editable_source` | 可编辑源文件（`.svg`/`.pptx`/`.docx`/`.mm`）路径与说明，元素要有名字 |
| `export_artifact` | 导出件（PNG/PDF/TIFF）路径、尺寸、dpi、渲染引擎 |
| `content_map` | 图上/页上的每个关键元素 → 材料来源定位符 |
| `compliance_checks` | 每条规则的**实测值**与判定（不要写“符合要求”，要写实测数字） |
| `exceptions` | 做不到的、材料缺失的、渲染失败的项，逐条写清 |

**必须同时交付可编辑源与真实渲染的预览**：预览不是“应该有”，而是磁盘上真实存在的文件。


## 2.5 时间与请求预算（先保交付，再谈完美）

评测侧的硬约束：**64 次模型请求、900 秒、单次回答 16384 tokens**。本 Skill 的流程按这个预算设计：

| 阶段 | 目标预算 | 必须落盘的东西 |
|---|---|---|
| 读契约 + 建索引 | ≤ 5% | `/app/work/blocks.json`（或等价中间表示） |
| 抽取 / 生成 / 修改 | ≤ 55% | `/app/work/` 下的中间产物 |
| **第一版完整结果** | **≤ 70%（≈ 10 分钟内）** | **`/app/output/result.json`（契约字段齐全，允许字段值为 `partial`）** |
| 校对与补漏 | 剩余 | 覆盖写 `result.json`，保留 `change_log`/`uncertainties` 的增量 |


规则：

1. **先写一版能过 `contract.py check` 的结果**，再改进。不允许把「还在读材料」当成完成态。
2. 每次覆盖 `result.json` 前先备份到 `/app/work/result.prev.json`，这样任何时刻磁盘上都有一份可用结果。
3. 值不值得再花一次请求，用这个判据：*这次调用能不能让某个字段从 `not_reported`/`partial` 变成有据可查的值？*
   不能就停手，把剩余时间用于写 `uncertainties` 与 `limitations`。
4. 大材料不要整篇塞进上下文：先建索引（定位符），只取与当前字段相关的块。
5. 宁可交一份「小但每条都能核验」的结果，也不要交一份「全但一半没定位符」的结果——
   评审按**可核验性**扣分，缺项只要如实标注就不算错。

## 3. 工作流程

1. 把源数据整理成 `data.json`（categories + series[].values + errors + n），每个数来自表格定位符，不改值。
2. 选图型并写清理由（比较/分布/相关/构成）；给 `chart` spec。
3. 生成：`figure.py chart spec.json -o fig.svg --png fig.png`（matplotlib 可用时出 PNG/PDF；不可用则 SVG 兜底并在 warnings 说明）。
4. 标注完整：轴标签+单位、量级（是否对数/归一化）、图例与系列一一对应、误差线含义（SD/SEM/CI）、n、显著性标记含义。
5. 对照校验：脚本把 spec 里的数值 vs 图元（柱高/点坐标）反算比对，输出 `compliance_checks`；一致才交付。
6. 导出 svg+png+pdf（可编辑源保留），图注写出数据来源与统计方法。
6. **单次回答保持简短**（≤ 300 字、≤ 2000 tokens）：评测里主要耗时是模型往返，不是脚本；不要复述文件内容、不要每步都总结，能一次 `python3 <脚本>` 解决的就不要拆成多轮对话。

每一步的中间产物都写到 `/app/work/`，最后一步才组装 `/app/output/result.json`；
中途被截断时，`/app/output/` 里必须已经有一份能过契约校验的部分结果。

## 4. 证据与诚实性规则（所有产出通用）

- 每条结论、每个字段都挂定位符：PDF 用 `p<页>b<块>`（由 `pdf_text.py` 生成），文本用段落/行号，代码用 `文件:行号`。
- 定位符只能来自**真的读过**的文件。没有读到的内容不许写：未报告写 `not_reported`，无法判断写 `unable`，
  材料冲突写 `conflict` 并给出两处原文。
- **缺失不是 0，不是默认值，不是常识补齐。** 单位缺失就不要补单位，n 没写就不要写 n。
- 字面命中不等于语义正确：引用到一句话之前，先确认这句话真的在陈述你要的结论（主谓宾、否定词、条件从句都要看）。
- 只读片段不够时读整段、整节、前后页；跨页续表先合并再取值。
- 先落一份**诚实的部分结果**（哪怕只有几项），再增量改进；不要把「能读通」当成「已核对」。


## 5. 异常与降级

| 情况 | 处置 |
|---|---|
| 扫描件/无文字层 | 记录 `no_text_layer`，报告可读页与不可读页；不要用模型记忆补正文 |
| 加密/权限受限 | 记录 `encrypted`，尝试 `qpdf --decrypt` 后再解析，仍失败如实报 `unable` |
| 图片型表格 | `pdf_text.py tables` 返回空后标注 `image_table`，不要手造行 |
| 乱码字体/私有编码 | 标注 `garbled_font`，给页码与原始字节片段，不猜字 |
| 页眉页脚混进正文 | 由工具标为 `furniture` 并排除，抽查 3 页确认 |
| 缺 office/matplotlib 等依赖 | 用本 Skill 的无依赖回退路径（`ooxml.py`、`figure.py` 的 SVG 兜底、`preview.py`），并在 `exceptions` 写明用的是哪条路径 |
| 材料与标签不符 | 以文件内容为准，在 `limitations` 写明标签与内容的差异 |


## 6. 交付前自检（先跑命令，再改内容）

```bash
# 1) 结果契约：字段齐不齐、枚举对不对
python3 "$SKILL_DIR"/scripts/contract.py check /app/output/result.json --normalize

# 2) 数字/单位有没有在改写中丢失或走形
python3 "$SKILL_DIR"/scripts/text_checks.py numbers <原文> <产出> 2>/dev/null || true

# 3) 引语是否真在材料里（把用到的引语写成 JSON 列表）
python3 "$SKILL_DIR"/scripts/evidence.py verify <blocks.json> <quotes.json>

# 4) 产物是否真的生成、能不能打开
ls -la /app/output /app/outputs 2>/dev/null
```

- `contract.py check` 的 `problems` 必须清空；`notes` 要逐条回应。
- `text_checks.py`/`evidence.py` 报出的每一条都要么修掉、要么写进 `uncertainties` 并说明原因。
- 声称存在的文件，`ls` 必须真的看得到。


## 7. 依赖与资源

- **必需**：Python 3.10+ 标准库。全部脚本在只有标准库时可运行。
- **可选（有则更好，无则自动降级并在输出里说明）**：`pymupdf`/`pdfplumber`/`pypdf`（更细的抽取）、
  `matplotlib`（数据图与保底预览）、`soffice`（office→PDF→PNG 预览）、`rsvg-convert`/`inkscape`（SVG 光栅化）、
  `pdftoppm`/`mutool`/`gs`（PDF 光栅化）、`tesseract`（OCR）。
- 安装（评测环境如可联网）：`pip3 install --break-system-packages pymupdf pdfplumber matplotlib`。
- 脚本之间只通过文件和标准输入输出耦合；**调用时统一写成 `python3 "$SKILL_DIR"/scripts/<name>.py`**，
  不要依赖当前工作目录。若某个脚本不存在（安装不完整），在 `exceptions` 里写明并用等价的手工步骤替代，
  不要假装它跑过。
- 文件数 ≤50、单文件 ≤500KB、总计 ≤2MB、无符号链接——本包已满足。


## 8. 最小示例（示意，不是正式案例结果）

```bash
mkdir -p /app/work /app/output
cat > /app/work/diagram.json <<'JSON'
{"title":"Example mechanism (synthetic)","nodes":[
  {"id":"lig","label":"Ligand","kind":"ellipse","color_index":0},
  {"id":"rec","label":"Receptor","kind":"receptor","color_index":1},
  {"id":"kin","label":"Kinase","kind":"box","color_index":2},
  {"id":"gene","label":"Target gene","kind":"process","color_index":3}],
 "edges":[{"from":"lig","to":"rec","type":"binding","label":"binds"},
          {"from":"rec","to":"kin","type":"activation","label":"activates"},
          {"from":"kin","to":"gene","type":"inhibition","label":"represses"}]}
JSON
python3 "$SKILL_DIR"/scripts/figure.py check  /app/work/diagram.json
python3 "$SKILL_DIR"/scripts/figure.py diagram /app/work/diagram.json -o /app/output/figure.svg
python3 "$SKILL_DIR"/scripts/preview.py diagram /app/work/diagram.json -o /app/output/figure.png --pdf /app/output/figure.pdf
python3 "$SKILL_DIR"/scripts/contract.py stub --family artifact -o /app/output/result.json
```

示意输出：`figure.svg`（可编辑，节点带 id 与 name）、`figure.png`（真实渲染，≥200dpi）、
`figure.pdf`（矢量）；`result.json` 按 artifact 契约填写 `editable_source`/`export_artifact`/`content_map`/
`compliance_checks`/`exceptions`。**以上均为示例，不含任何正式案例结果。**


## 9. 本包内的脚本

`figure.py`（示意图与数据图，SVG 优先）、`ooxml.py`（无依赖 pptx/docx 导出）、`render.py`（office→PDF→PNG）与 `preview.py`（无 office 时的保底渲染）、`evidence.py`、`contract.py`、`gain_report.py`（增益量化）、`summary_card.py`（结果可视化摘要）


## 10.1 组织方 workorder（逐字保留，必做项）

> Concrete source-bound drawing workorder: source_value_chart. Primary biological sources for figure03; actual source data for charts. Original own16rubric unchanged. Real editable SVG/PNG/PDF and source-preserving edit required; scientific judgement remains native/LLM Judge, not a literal quote or export pass.

把这段翻译成三个硬要求，缺一条即视为未完成：

1. **source-bound**：图里每个实体、关系、数值都要指回组织方给的主来源（figure03 对应的原始文献/数据），不是从通用知识生成；`content_map` 里给 locator。
2. **真实可编辑的 SVG/PNG/PDF 三件套**：SVG 必须保留可编辑文字与命名元素、可按 id 定位；PNG/PDF 必须是真实渲染（不是改扩展名）。
3. **source-preserving edit**：必须有一条「打开已有源文件 → 只改指定元素 → 保留全部 id/分组/未改动字节」的路径，并留下逐项 diff。用 `figure.py inspect` 列出可编辑元素，`figure.py edit --patch` 执行编辑，它会回报 `applied`/`ids_preserved` 并拒绝无改动或无 id 的源。

```bash
python3 "$SKILL_DIR"/scripts/figure.py inspect existing.svg            # 列出 node-/edge-/label- id
python3 "$SKILL_DIR"/scripts/figure.py edit existing.svg \
    --patch ops.json -o edited.svg --png edited.png                    # 只改 patch 点名的元素
```


## 10.5 用可测量的方式说明增益（不要只写「本 Skill 更好」）

评分里有一项是「增益可验证性」。它要求对照，不要求形容词。本包提供 `gain_report.py`，
它把「加载本 Skill 之后到底变了什么」变成磁盘上的数字：

```bash
# 无 Skill 的产出（基线）与本 Skill 的产出，各跑一次
python3 "$SKILL_DIR"/scripts/gain_report.py report --result /app/work/baseline/result.json \
  --blocks /app/work/blocks.json --material /app/work/material.txt -o /app/work/gain_baseline.json
python3 "$SKILL_DIR"/scripts/gain_report.py report --result /app/output/result.json \
  --blocks /app/work/blocks.json --material /app/work/material.txt -o /app/work/gain_skill.json
python3 "$SKILL_DIR"/scripts/gain_report.py compare /app/work/gain_baseline.json /app/work/gain_skill.json \
  -o /app/output/gain.json
```

它量化五件事，全部来自文件而不是自述：

| 指标 | 含义 | 期望方向 |
|---|---|---|
| `contract_problems` | 结果契约缺项数 | → 0 |
| `locator_coverage` | 带真实定位符的字段/论断占比 | 越高越好 |
| `evidence_exactness` | 引语能在材料里逐字命中的比例 | 越高越好 |
| `honesty_labelling` | 未报告/无法判断被如实标注的比例 | 越高越好 |
| `fabrication_risk` | 产出中在材料里找不到的数字占比 | 越低越好 |

跑不出基线（案例只允许一种条件）时，就**只报本 Skill 的绝对指标**，并在 `limitations` 说明
对照条件不满足——不要编造一个基线数字。


## 11. 本题自查清单

- [ ] 每个图元可反算回源数据
- [ ] 单位/量级/误差线含义齐全
- [ ] 图例与系列一致
- [ ] 中文标注未乱码
- [ ] 可编辑源与位图同时交付
