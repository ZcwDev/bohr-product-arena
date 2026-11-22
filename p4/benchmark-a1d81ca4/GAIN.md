# 使用增益（同输入 · 同模型 · 真跑对照）

同一组输入（4 题 + 4 条候选答案 + 4 条参考答案）：
**A = 不加载 Skill**（把三类输入直接交给 `qwen4-max`，要求逐题打分 + 聚合）；
**B = 加载本 Skill**（`scripts/bench_toolkit.py`）。A 的原始输出在 `example/baseline_score.txt`。

| 指标 | A（无 Skill） | B（有 Skill） |
|---|---|---|
| 逐题机器可读分数 | 4 / 4 | 4 / 4 |
| 聚合指标 | 有（单行 `aggregate`） | 有（`n_items / scored / mean / pass_rate / total / max_total`） |
| 失败分类（可计数、可点名题目） | **无**（I4 只写 `fail`，没有类别） | **有**（`empty_output ×1 → I4`） |
| 输入哈希 / 可追溯 | **无** | 有（3 个文件的 `sha256_16`） |
| 运行历史与回滚锚点 | **无** | 有（`run_history.jsonl` 两次运行，`delta=0.0`，`rollback_to` 指向上一版） |
| 确定性自检 | **无** | 有（同输入再评一遍，逐题一致 `deterministic_rerun=true`） |
| 产物形态 | 一段回答（无法作为工具复用） | `result.json` + `per_item.tsv` + `failure_analysis.md` + `run_history.jsonl` |

**结论（只写实测）**
1. 单次打分的准确性两边一致（这次 4/4 题都对）——增益不在"算得对"，在**能不能当评测工具长期用**。
2. A 的失败项只有 `fail` 两个字，无法归因；B 把 I4 归到 `empty_output`，可直接转成下一版题集的专项题。
3. A 无法回答"这次分数和上次比变了吗、能回滚吗"；B 有输入哈希 + 运行历史 + `version_delta`。
4. **没有改善的地方**：开放式答案仍需 LLM/人工评分；本工具只做字符串/数值等价比对（`limitations` 已写）。
