---
name: field-level-evidence-extraction
description: 按字段表逐条抽取文献里的结论与数据：每个字段给出值、单位、统计量、实验条件、对照、逐字引文与段落定位，并区分 reported / not_reported / source_conflict / extraction_failed 四态。当任务给出 target-fields.json 要求"抽字段/填表/提取结论与数据"时使用；缺失不等于 0、字面命中不等于语义正确。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/extract_fields.py --input /input --output /app/output
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

# 文献结论与数据提取（字段级）

给定**字段表**与**原始文献**，逐字段产出可复核的抽取值：值 / 单位 / 统计量 / 实验条件 / 对照 / 逐字引文 / 段落定位 / 状态。

## 何时用

- 输入里有 `target-fields.json`（要抽哪些字段），材料是论文 PDF/文本
- 需要"填表式"产出：每行一个字段，值可回原文核对
- 需要区分"没报告"与"抽不出来"（这两者不能都写成 0）

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/extract_fields.py" --input /input --output /app/output
```

输入：

| 路径 | 内容 |
|---|---|
| `/input/target-fields.json` | `[{"field_id","field"/"name"/"description","units"}]`，必须读取 |
| `/input/case.json` | 可选，`case_id` 必须逐字进结果 |
| `/input/**/*.pdf|txt|md` | 真实论文（评测时提供，无预抽取答案层） |

输出（`/app/output/`）：`result.json`（`result.fields[]` 每字段一条）、`fields.tsv`（机读表）、
`field_cards.md`（人读卡片）、`SELFCHECK.json`（引文回原文校验 + 状态枚举 + "缺失不写 0"校验）。

## 四态定义（必须严格区分）

| 状态 | 含义 |
|---|---|
| `reported` | 找到逐字引文，且同时具备**值**与（单位｜统计量｜实验条件）中的至少一项 |
| `not_reported` | 材料里没有该字段的句子；**不写 0、不写空字符串** |
| `source_conflict` | 同一字段在不同位置/来源给出不同数值；需并列双方与条件 |
| `extraction_failed` | 只命中"主题相关"的句子，缺单位/统计量/条件，无法判定为真实抽取 |

## 方法（脚本实现）

1. **定位**：PDF 走 PyMuPDF 取文本层（失败退回 `pdftotext`，再失败就记不可读，不猜内容），
   按"页 → 段落块"切分，块带定位符 `p{页}b{块}`。
2. **候选**：字段名/描述/单位作为查询，句级实词重叠 ≥0.3 入选，数值句与带单位句加权。
3. **抽取**：抽数字（**排除标识符里的数字**，如 `HCT-116`、`IL-6` 不是测量值）、单位、统计量
   （`p<…`、`n=…`、`±…`、`95% CI`）、条件（剂量/时长/模型）、对照（vs vehicle/control/baseline）。
4. **判定**：数字必须配单位/统计量/条件之一才算 `reported`；否则 `extraction_failed` 并给出理由；
   同字段多候选数值不同 → `source_conflict` 并列。
5. **自检**：每条引文都要能在提取文本里逐字找回（`SELFCHECK.json.quotes_not_found` 必须为空）；
   `no_zero_for_absent=true` 表示没有把缺失写成 0。

## 异常与边界（写进 uncertainties / limitations）

- 扫描件/无文本层：`extraction_failed` + 说明需要 OCR，**不凭标题补值**。
- 数值只在图注或图内：明确写"数值仅见于图注/图内"，必要时判 `extraction_failed`。
- 同一字段多组实验（不同剂量/时间）：必须把条件写进 `condition`，否则同值不同条件会被误判冲突。
- 转引（secondary citation）无法回到原文：`not_reported` 并注明只找到转引。

## 资源与依赖

- 唯一入口 `scripts/extract_fields.py`：纯标准库 +（可选）PyMuPDF；无 PyMuPDF 自动退回 `pdftotext`。
- 无网络、无私有路径、不写任何基准分数。

## 最小调用示例（示例，不是正式案例结果）

`example/` 下有一份 2 段示例文献与 4 个示例字段（`EXAMPLE-CASE-001`）：

```bash
python3 scripts/extract_fields.py --input example --output example-output
# F1 reported 312 mm3（±41, day 21）/ F2 reported 12 nM / F3 not_reported / F4 reported -1.2 %
```

示例输出仅说明接口与四态判定，**不得**当作正式案例答案。
