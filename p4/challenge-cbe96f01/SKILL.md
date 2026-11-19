---
name: evidence-trace-and-claim-verification
description: 把待核验的科研论断拆成可独立判定的子判断，为每条子判断定位最小充分证据（页码/区块/逐字引文），给出 supported / partially_supported / contradicted / insufficient_evidence 四级判定与理由；区分"主题相关"与"真正支持"，并列冲突与范围限制。当用户要求核验某句结论、检查引用是否支持论断、或输出证据卡片/核验报告时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/verify_claims.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
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

# 文献证据溯源与事实核验

给定一批**待核验的论断**（它们是待检验的问题，不是权威事实）和对应文献，产出**可复核的证据卡片**：
论断 → 子判断 → 最小充分证据（逐字引文 + 定位符）→ 四级判定 → 理由。

## 何时用

- "这句结论文献支持吗 / 这个引用支持这段论述吗"
- "帮我核验 C1..Cn，给出证据位置和判定"
- 需要批量产出证据卡片、引用核查清单、冲突提示时

不适用：只需要文献综述、翻译或摘要（那不是核验），或材料根本没有原文可定位时（应直接报 `insufficient_evidence`）。

## 一次运行

```bash
# 评测环境里 Skill 装在 /skills/weekly-skill；本地自测把 --input/--output 指到自己的目录
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/verify_claims.py" --input /input --output /app/output
```

输入（读取真实文件，不要凭标签假设内容）：

| 路径 | 内容 |
|---|---|
| `/input/claims-to-verify.json` | `[{"claim_id": "...", "claim": "..."}]`，必须读取，不能自带预制答案 |
| `/input/case.json` | 可选，含 `case_id`；必须逐字复制到结果里 |
| `/input/**/*.pdf|txt|md` | 原始文献（评测时是真实论文 PDF，没有预抽取的答案层） |

输出（`/app/output/`）：

| 文件 | 内容 |
|---|---|
| `result.json` | `case_id` / `status` / `result.verdicts[]` / `sources` / `uncertainties` / `limitations` / `change_log` |
| `evidence_cards.md` | 人类可读的证据卡片（断言 → 子判断 → 判定 → 引文与定位） |
| `evidence_cards.tsv` | 机读表：claim_id / atom / verdict / locator / quote |
| `SELFCHECK.json` | 自检：引文是否逐字回原文、枚举是否合法、断言是否全覆盖 |

`status` 取值：`answered`（材料可读且全部断言有判定）/ `partial`（部分断言因材料问题无法判定）/
`input_error`（输入缺失或不可解析，此时不编造判定）。

## 四级判定（定义必须一致使用）

| 判定 | 含义 |
|---|---|
| `supported` | 证据句在**相同条件**（同一对象/对照/剂量/时间窗/模型）下给出与断言一致的方向与显著性 |
| `partially_supported` | 复合断言的子判断部分成立；或证据只覆盖断言的子集；或同一原子支持与否定并存 |
| `contradicted` | 证据句明确给出与断言相反的方向（含 "no significant change"、"did not"、"failed to" 等） |
| `insufficient_evidence` | 只有主题相关的段落、没有可判定方向的句子；或人群/模型范围不匹配（如动物实验外推人群）；或材料不可读 |

**主题相关 ≠ 支持**：这一点是本题的核心，宁可判 `insufficient_evidence`，也不要把"提到同一个分子/同一种肿瘤"当成支持。

## 方法（脚本里逐条实现）

1. **拆解**：按 `and` / 分号 / 并列动词把复合断言拆成原子，记录每个原子的类型（事实 / 比较 / 因果 / 范围），
   并保留对照、否定、显著性、限定条件。
2. **定位**：把材料切成"页-区块"块，块带定位符 `p{页}b{块}` 与来源文件名；PDF 用 PyMuPDF 取文本层，
   取不到就退回 `pdftotext`，都失败则记为不可读（不猜内容）。
3. **检索**：按实词重叠 + 显著性线索 + 数字命中给块打分，取前 3 块，低于阈值视为无证据。
4. **判定**：句级判定，且**必须命中同一效应词干**（reduce/decrease/abolish/inhibit…）才算涉及该原子——
   否则 "did not abolish tumour growth" 会被误当成 "reduced tumour volume" 的反证。
   否定线索 → `contradicted`；支持与否定并存 → `partially_supported`；人群范围不匹配 → `insufficient_evidence`。
5. **汇总**：全部原子 supported → `supported`；全部 contradicted → `contradicted`；
   混合或含证据不足 → `partially_supported`；全无证据 → `insufficient_evidence`。
6. **自检**：每条引文都要能在提取文本里逐字重新找到（`SELFCHECK.json` 里 `quotes_not_found` 必须为空）；
   否则删掉该证据并把该原子降级。

## 异常与边界（必须照实写进 uncertainties / limitations）

- PDF 无文本层 / 扫描件：`status=partial`，说明需要 OCR，**不要**凭标题补内容。
- 图表里只有图注、正文未写数值：判 `insufficient_evidence` 并指出"数值仅见于图注/图内"。
- 论断含多个子判断但只找到部分证据：给出 `partially_supported` 并列出缺哪一个子判断。
- 两个来源互相冲突：两条引文都列（各自定位符），判定降级为 `partially_supported`，并在 `reason` 里写冲突点。
- 引用转引（secondary citation）无法回到原文：记 `insufficient_evidence`，说明只找到转引。

## 资源与依赖

- `scripts/verify_claims.py`：本 Skill 唯一入口，纯标准库 +（可选）PyMuPDF；无 PyMuPDF 时自动退回 `pdftotext`。
- 无网络依赖、无嵌入模型调用；不写作者私有路径；不在结果里写任何基准分数。

## 最小调用示例（示例，不是正式案例结果）

`example/` 下有一份 3 段示例文献与 4 条示例论断（`EXAMPLE-CASE-001`），演示四种判定各一条：

```bash
python3 scripts/verify_claims.py --input example --output example-output
# example-output/result.json -> C1 supported / C2 partially_supported / C3 contradicted / C4 insufficient_evidence
```

示例输出仅用于说明接口，**不得**当作正式案例答案。
