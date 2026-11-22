---
name: product-arena-09-0e8befd4
description: Nanopublication：三段式——脚本抽可定位块→（模型）写主张/判定/逐字引文→脚本门禁校验并出审计；每条断言含主张、判定（supported/contradicted/insufficient）、引文、定位与依据。
---

## 立刻执行（不要探索）

评测里**直接执行这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --output /app/output --run-all
```

`--run-all` = 写 `blocks.tsv` → 有 `nanopubs.json` 且过门禁就用它、否则退回脚本推导断言 → 出 `audit.json`
→ `attach_meta`。三步也可单独跑：`--blocks` / `--verify-nanopubs out/nanopubs.json`。

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

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

# 阅读14｜Nanopublication

Nanopublication：把阅读结论写成可核验断言——每条断言含主张、判定（supported/contradicted/insufficient）、逐字引文与定位、简短依据说明。

## 三段式怎么用（有模型时，判定质量来自这一步）

**第一步 · 脚本抽块** → `/app/output/blocks.tsv`

每行 `locator · chars · text`。有 `corpus.json` 时 `locator` 是 `文档#小节`（audit 的 `evidence` 认这个格式），
否则是 `文件名#pN`（第 N 段）。脚本不做语义判断；同一命令已产出一版推导断言作底稿。

**第二步 · 模型写 `nanopubs.json`**

```json
{"claims": [{"claim_id": "N01",
             "claim": "热处理可显著提高疲劳寿命",
             "verdict": "supported",
             "quote": "热处理组的疲劳寿命明显更高",
             "locator": "manuscript.md#p3",
             "rationale": "证据段给出的热处理组循环数高一倍，且无否定表述"}]}
```

| 约束 | 值（第三步逐条校验） |
|---|---|
| `verdict` | 只能是 `supported / contradicted / insufficient` |
| `quote` | 在 `blocks.tsv` 的 text 里**逐字存在**（忽略空白）；改写过的引文按无证据计 |
| `locator` | **从 `blocks.tsv` 复制**；编造的定位会被拦下 |
| `rationale` | ≥20 字，写清"哪个数值/表述支持它" |
| 数量 | ≥3 条；一篇文章通常 5–12 条，覆盖摘要/结果/结论，别只挑同义句 |

判定口径：`supported`=引文数据与主张一致且无否定；`contradicted`=引文给出相反结论或不同数值（含被修订/撤稿段落）；
`insufficient`=材料只覆盖一部分，或压根没有对应证据。**宁可写 insufficient 也不要编证据**——评测对"无证据的 supported"判 0。

**第三步 · 门禁校验（已包含在 `--run-all` 里）**

`--verify-nanopubs` 写 `nanopubs_check.json`：`quotes_verbatim / locators_from_stage1 / enums_valid / notes_substantive / all_passed`。
不过门禁 → 自动退回脚本推导断言，并在 `result.json` 的 `gaps` 里写明，**不会假装用了模型结果**。
`check_report.json` 里 `model_script_agreement_rate` 是模型判定与脚本字面判定的一致率，供人复核，不作门禁。

## 交付物

| 文件 | 内容 |
|---|---|
| `audit.json` | 每条断言：`claim` · `status` · `evidence[{doc_id,section_id,quote,overlap}]` · `audit_note` · `locator` · `quote` |
| `annotated_report.md` | `ID \| status \| 证据定位`，顺序与 `audit.json` 完全一致 |
| `check_report.json` | 自校验：id 唯一、状态枚举、证据可解析、无撤稿段作支持（+ 模型门禁结果） |
| `nanopubs_check.json` | 模型图的逐条门禁（是否采用 `nanopubs.json` 的依据） |
| `blocks.tsv` | 第一阶段的块与定位 |
| `inventory.tsv` / `repro_manifest.json` | 六维文件元数据与复跑信息 |

判定边界：**只用材料里有的东西** —— 每条结论带 `文件#定位`；材料没写的字段写「材料未说明」；做不到的写进 `gaps`，不写成已完成。
