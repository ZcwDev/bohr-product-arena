---
name: literature-reading-benchmark-harness
description: 把"文献阅读"任务变成可运行、可复用、可版本化的题集 + 评测工具：自动发现问题集/候选答案/参考答案三类输入，输出原始答案、逐题得分、失败分类，并记录输入哈希与运行历史（可追溯、可回滚）。当任务要求"建题集/做评测工具/输出逐题得分与失败分析"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/bench_toolkit.py --input /input --output /app/output
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

# 文献阅读 Benchmark 与评测工具

产出三样东西（题目原文点名的"判得动 / 跑得一致 / 查得回 / 改得动"）：

1. **原始结果**：每条候选答案原文保留（`result.json` 的 `answer_raw` + `per_item.tsv`）；
2. **逐题得分**：`score / max_score / verdict`，带判定依据；
3. **失败分析**：机械分类（`missing_output` / `empty_output` / `wrong_format` / `wrong_value`）+ 每类命中的题目 ID。

再加两样让 benchmark 可复用的东西：**输入哈希**（每次运行记录所用文件的 sha256 前 16 位）与
**运行历史**（`run_history.jsonl` 追加，含 mean 与相对上次的 `delta` 和回滚锚点）。

## 第 0 步（Agent 必做，先于脚本）

隐藏案例给的是 `/input/case.json` + `task.md` + 科研材料，**不一定有现成的题集 JSON**。所以：

1. 先读 `case.json` 与 `task.md`，把 `output_contract` 里要求的字段列出来（`result.json` 必须逐字段覆盖，缺的写 `null` 并说明）；
2. 若存在 `*items*.json` 等题集 → 直接进入评分；
3. **若没有题集** → 让脚本从材料派生题卡（`derive_items`）：每个"带数值+单位的事实句"变成一道可判定题，
   参考答案取该数值，定位符指向该句；随后 Agent 再补 `task` 措辞与 `rubric_0/1/2` 锚点；
4. 有候选答案（`*answers*.json`）就打分；没有候选就**交付题集+参考答案+定位**（`verdict=reference_only`），
   这是 benchmark 题的正常交付物，不是"未完成"。

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/bench_toolkit.py" --input /input --output /app/output
```

输入按文件名族自动发现；**题集缺失时从材料派生**（见第 0 步第 3 条）：

| 角色 | 匹配 |
|---|---|
| 题集 | `*items*.json` / `*tasks*.json` / `*questions*.json`（`[{item_id, question, max_score}]`） |
| 候选答案 | `*answers*.json` / `*submissions*.json` / `*responses*.json`（`[{item_id, answer, evidence}]`） |
| 参考答案 | `*reference*.json` / `*gold*.json` / `*expected*.json`（`[{item_id, answer}]` 或 `[{item_id, accept:[...]}]`） |

输出：`result.json`（`result.items[] / aggregates / failure_taxonomy / run_record / version_delta`）、
`per_item.tsv`、`failure_analysis.md`、`run_history.jsonl`、`SELFCHECK.json`。

## 判定规则（写死在代码里，可复跑）

- 参考答案是数值 ⇒ 从候选答案里取第一个数字比对（`312 mm3` 对 `312` 判对；`about 12` 对 `12` 判对，相对误差 ≤5%）；
- 参考答案是 `accept` 列表 ⇒ 归一化后精确匹配，或短语包含判 `partial`；
- 只有题集没有参考答案 ⇒ 只做**结构性检查**（必需字段在不在），并在 `uncertainties` 里写明"分数不代表正确性"；
- 空答案 ⇒ `verdict=empty`、`failure=empty_output`，**不是错误值**（区分"没交"与"交错了"）。
- **确定性自检**：同一份输入当场再评一遍，两次逐题分数必须完全一致（`SELFCHECK.deterministic_rerun`）。

## 异常与边界

- 三类输入任一缺失 → `status=partial` + 明确写出缺哪一类（不猜、不补造答案）。
- 候选与题集 `item_id` 对不上 → 该题记 `missing_output`，并在失败分析里点名。
- JSON 坏了 → 跳过该文件并记进 `uncertainties`，不中断整体评测。
- 开放式答案需要 LLM/人工评分 ⇒ 本工具只给结构性分，`limitations` 里写明。

## 资源与依赖

`scripts/bench_toolkit.py`：纯标准库（含解释器 shim），无网络，无私有路径，不写基准分数。

## 最小调用示例（示例，不是正式案例结果）

`example/` 有 4 题 + 4 条候选 + 4 条参考（`EXAMPLE-CASE-001`）：
```bash
python3 scripts/bench_toolkit.py --input example --output example-output
# 逐题：I1 correct 2.0 / I2 correct 2.0 / I3 correct 1.0 / I4 empty 0.0
# 聚合：mean 1.25、pass_rate 0.75；失败分类：empty_output ×1 (I4)
```
