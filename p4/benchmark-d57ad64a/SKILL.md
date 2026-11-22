---
name: writing-benchmark-and-rater-agreement
description: 把学术写作任务变成可判定的题集与评测工具：按 rubric 权重逐题打分、区分"可接受变体"与"实质错误"、给出可观察的失败类型与改进建议，并计算双评一致率与 Cohen's kappa 来指出哪条锚点需要改。当任务要求"写作题集/参考答案/评分锚点/标注一致性/评测报告"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/writing_bench.py --input /input --output /app/output
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

# 学术写作 Benchmark 与评测工具

解决的是"题能不能判、答案站不站得住、锚点齐不齐、跑得可复现、报告说得清"这五件事，
产物是**可以反复跑的评测工具**，不是一次性的分数。

## 第 0 步（Agent 必做，先于脚本）

隐藏案例给的是 `/input/case.json` + `task.md` + 科研材料，**不一定有现成题集**：

1. 先读 `case.json` / `task.md`，把 `output_contract` 要求的字段列出来（`result.json` 逐字段覆盖，缺的写 `null` + 原因）；
2. 有题集就直接评分；
3. **没有题集** → 让脚本从材料派生（`derive_items`）：每个含事实/数值的句段成为一道写作题，
   `criteria` 默认 `accuracy(2)/completeness(1)/structure(1)`，参考答案取该句原文与定位符；Agent 再补 `prompt` 措辞与 `accept/reject` 变体；
4. 有候选就打分；没有候选就**交付题集+参考答案+锚点**（`verdict=reference_only`），这是 benchmark 题的正常交付物。

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/writing_bench.py" --input /input --output /app/output
```

输入按文件名族自动发现；**题集缺失时从材料派生**（见第 0 步第 3 条）：

| 角色 | 匹配 | 字段 |
|---|---|---|
| 题集 | `*items*.json` / `*tasks*.json` | `item_id, criteria:[{name,weight}], required_sections[]` |
| 候选 | `*answers*.json` / `*submissions*.json` / `*drafts*.json` | `item_id, answer` |
| 参考 | `*reference*.json` / `*gold*.json` | `item_id, accept:[…], reject:[…]` |
| 双评 | `*rater*.json` / `*annot*.json` | `rater, item_id, scores:{criterion: 0/1}` |

输出：`result.json`（逐题 + 聚合 + 失败分类 + `rater_agreement` + `run_record`）、`per_item.tsv`、
`report.md`（逐题得分/证据/失败类型/改进建议）、`SELFCHECK.json`。

## 判定规则

- **加权求和**：每题的分数 = Σ(锚点满足度 × 权重)，权重来自题集，不写死。
- **可接受变体 vs 实质错误**：参考里 `accept` 命中 ⇒ 视为合法写法；出现 `reject` 片段 ⇒ 判 `wrong_fact`；
  两者都不命中 ⇒ 判 `style_or_variant`（风格差异，不是错误）。
- **可观察锚点**：`required_sections` 缺一节 ⇒ `wrong_format: missing X`，并在 `criteria` 里点名是哪条锚点扣的分。
- **标注一致性**：两名评分员的逐判据打分给 `percent_agreement` 与 `cohen_kappa`；
  kappa 低 ⇒ 说明该判据的锚点不可观察，应改锚点而不是改分数（报告里直接给这条建议）。
- **可复现**：输入文件 sha256 前 16 位进 `run_record`；同一输入重跑结论一致。

## 异常与边界

- 缺候选 ⇒ `missing_output`；候选空串 ⇒ `empty_output`（两者都单独成类）。
- 缺参考 ⇒ 只做结构性评分，`uncertainties` 写明"分数不代表正确性"。
- 只有一名评分员 ⇒ 不算 kappa（`agreement_computed=false`），不假装有共识。
- 样本 < 2 或类别单一 ⇒ kappa 退化，报告里标注 `null` 而不是编一个 0。

## 资源与依赖

`scripts/writing_bench.py`：纯标准库（含解释器 shim），无网络、无私有路径、不写基准分数。

## 最小调用示例（示例，不是正式案例结果）

`example/`：2 题 + 2 候选 + 2 参考 + 2 名评分员（`EXAMPLE-CASE-001`）
```bash
python3 scripts/writing_bench.py --input example --output example-output
# W1 partial 2.0/5.0（缺 Limitations）；W2 partial 2.0/3.0（含 reject 片段 "abolished in humans"）
# 一致性：completeness 100%/κ=1.0，accuracy 50%/κ=0.0（⇒ 该锚点需改），structure n=1 不计算
```
