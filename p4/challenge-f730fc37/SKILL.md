---
name: weekly-challenge-f730fc37
description: 当任务属于「阅读客户端」这一类——交付一个可被 Agent 与人都能用的“阅读客户端”式能力：统一入口命令 + 稳定的中间表示（份文档清单/目录/段落索引…——需要按案例契约产出可核验结果时使用；提供可执行流程与机检器，所有结论回到材料定位符。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output \
  --work /app/work/run
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。
- `run.py` 支持 `--authoring <file>` 传入人工/模型写好的补充内容；没有该文件时只用材料里的事实。


# 阅读客户端｜每周技能（weekly-skill）

本 Skill 面向「阅读客户端」这一类任务：当任务属于「阅读客户端」这一类——交付一个可被 Agent 与人都能用的“阅读客户端”式能力：统一入口命令 + 稳定的中间表示（份文档清单/目录/段落索引…——需要按案例契约产出可核验结果时使用；提供可执行流程与机检器，所有结论回到材料定位符。

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

交付一个可被 Agent 与人都能用的“阅读客户端”式能力：统一入口命令 + 稳定的中间表示（份文档清单/目录/段落索引/证据面板）+ 可离线复跑，而不是一段提示词。


## 2. 输出契约（reading 家族）

`/app/output/result.json`（同时镜像一份到 `/app/outputs/result.json`）：

| 字段 | 要求 |
|---|---|
| `case_id` | 原样保留案例给的 id |
| `status` | `answered` / `partial` / `insufficient_evidence` / `input_error` |
| `result` | 题目主结果：结构化字段，按键取值 |
| `claims` | 列表，每条含 `kind`、`evidence`（`{locator, quote}`）、`source_id`、`location` |
| `uncertainties` | 未报告、无法判断、材料冲突、需要人工复核的项 |

非 `answered` 时必须写 `limitations`：缺什么、影响哪一部分、人工从哪里接管。


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

1. 定义客户端入口：`python3 scripts/reader.py index|search|show|cite|digest`，每个子命令只用标准库，输入是材料目录或单文件。
2. 建立文档中间表示：`docindex.json`（文档清单 + 标题 + 页数 + 章节树 + 块定位符），供后续子命令复用。
3. search：关键词/短语检索返回定位符与上下文；show：按定位符打印原文，保证与原文逐字一致。
4. cite：把一段结论转成可粘贴的引用行（作者/题名/出处/年份/定位符），缺项写 `not_reported`。
5. digest：按章节输出结构化摘要骨架（不写结论，只给框架 + 定位符），便于人工补。
6. 把客户端用法写进 SKILL.md 与 README，附一段最小示例输入与示意输出（标注为示例）。

实际命令（材料目录或单文件都行）：

```bash
S="$SKILL_DIR/scripts"
python3 "$S"/reader.py index /input/materials -o /app/work/docindex.json \
        --jsonl /app/work/blocks.jsonl          # -> docs / blocks / warnings 计数
python3 "$S"/reader.py search /app/work/docindex.json "lipid titer" -n 10
python3 "$S"/reader.py show   /app/work/docindex.json p1b4        # 原样打印，不加修辞
python3 "$S"/reader.py cite   /app/work/docindex.json p1b4        # 缺作者/年份/DOI 就写 not_reported
python3 "$S"/reader.py digest /app/work/docindex.json -o /app/work/skeleton.json
```

- `index` 的 `source_id` 是相对材料根目录的路径；材料多于一份时，定位符自动加前缀 `<source_id>:p1b4`，
  否则保持 `p1b4`，两种写法 `show`/`cite` 都认。
- `search` 命中返回 `locator` + 原文大小写不变的 `context`；0 命中时退出码为 1（不是错误，是没有）。
- `show` 对文本/代码文件输出的是文件原始字节切片（含换行），可与 `sed -n` 对照；
  对 PDF 输出的是抽取器还原的块文本——PDF 本身没有可分离的「原文字符串」，所以同时报 `engine`。
- `digest` 只给骨架（章节标题 + 定位符 + 块跨度），**不写结论**。

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
python3 "$SKILL_DIR"/scripts/pdf_text.py extract /app/work/example.pdf -o /app/work/blocks.json --jsonl /app/work/blocks.jsonl
echo '[{"id":"c1","quote":"reached 42.5 mg/L after 24 h"}]' > /app/work/quotes.json
python3 "$SKILL_DIR"/scripts/evidence.py locate /app/work/blocks.json "42.5 mg/L" --limit 3
python3 "$SKILL_DIR"/scripts/evidence.py verify /app/work/blocks.json /app/work/quotes.json
python3 "$SKILL_DIR"/scripts/contract.py stub --family reading -o /app/output/result.json
```

示意输出：`blocks.json` 里每个块带 `locator`（`p<页>b<块>`）与 `type`（body/caption/reference/furniture…）；
`quotes.json` 的核验结果给出 `found`/`exact`/`locator`；`result.json` 按 reading 契约填好后
`contract.py check` 输出空的 `problems`。**以上均为示例，不含正式案例结果。**


## 9. 本包内的脚本

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `reader.py` | 客户端入口：`index`/`search`/`show`/`cite`/`digest` | 仅标准库（PDF 走 `pdf_text.py`） |
| `pdf_text.py` | 带 `p<页>b<块>` 定位符的 PDF 抽取；`tables`/`refs` 子命令 | 标准库；可选 pymupdf/pdfplumber/pypdf/pdftotext |
| `evidence.py` | 定位、引语核验（`exact`）、断言拆分、数字+单位扫描 | 仅标准库 |
| `text_checks.py` | 数字/术语/引用/去重/断言/结果 六类机械检查 | 仅标准库 |
| `contract.py` | 结果契约校验（`check`/`stub`/`merge`） | 仅标准库 |
| `gain_report.py` | 增益量化（`report`/`compare`） | 仅标准库 |
| `ooxml.py` | docx/pptx/xlsx 文本与表格抽取（无 office 依赖） | 标准库 zipfile+xml |
| `figure.py` | 数据图与示意图，matplotlib 缺失时回退 SVG | 可选 matplotlib |
| `preview.py` / `render.py` | 版面预览与 SVG/PDF 光栅化 | 可选 soffice/rsvg/inkscape/pdftoppm |
| `summary_card.py` | 一页结论卡片 PNG | 可选 matplotlib |

`gain_report.py --contract` 与 `contract.py --contract` 都接受两种文件：
直接是契约对象，或案例文件 `{"case_id":...,"output_contract":{...}}`（会自动解包内层）。

### 9.1 不要用 `-I` 跑这些脚本

`python3 -I` 会隐藏用户 site-packages。实测（Python 3.9.6）：`-I` 下 `import fitz` 和
`import matplotlib` 都报 `ModuleNotFoundError`，`pdf_text.py` 会静默降级到 `raw-stream`
并把 `blocks` 抽成 0 条——看起来「跑通了」，实际什么都没读到。

**统一用 `python3 "$SKILL_DIR"/scripts/<name>.py`**。若不得已用 `-I`，先验一条：

```bash
python3 -c "import fitz" && echo fitz-ok   # 有输出才说明能升级引擎
```

并在输出里写明降级路径（`engine` 字段会说真话）。


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

- [ ] 入口命令可离线运行且退出码为 0
- [ ] index 结果可被后续子命令复用
- [ ] show 输出与原文逐字一致
- [ ] 缺项如实标注
- [ ] 示例明确标为示例
