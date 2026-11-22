---
name: product-arena-10-760d1a0b
description: 文献知识图谱构建：三段式——脚本抽块与定位→（模型）写有类型的节点/关系→脚本校验并渲染可编辑图（SVG/PNG）；节点与边都能逐字回到原文定位。
---

## 立刻执行（不要探索）

评测里**直接执行下面这一整块**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/mindmap.py --input /input --output /app/output --run-all
```

`--run-all` = 抽块（`blocks.tsv`）→ 有 `graph.json` 且过门禁就用它、否则退回推导图谱 → 渲染 → `attach_meta`。
三步也可单独跑：`--blocks` / `--verify-graph out/graph.json` / `--graph out/graph.json`。

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。
- `graph.json` 不存在或未通过校验时，上面的 `if` 自动退回脚本推导图谱——**任何情况下都不能只交一个空目录**。

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

# 阅读15｜文献知识图谱构建

文献知识图谱构建：从材料抽节/断言/量值与有类型的实体关系（支持/反驳/因果/部分于），输出可编辑图（SVG/PNG）与结构化图数据，节点边都可回到原文定位。

## 三段式怎么用（有模型时，一次判断换一次质变）

**第一步 · 脚本抽块（已在上面的命令里执行）** → `/app/output/blocks.tsv`

每行 = `locator · chars · text`。`locator` 形如 `manuscript.md#p1b7`，这是唯一合法的定位；
脚本只做切块、分节与定位，不做语义判断。同一命令已产出一版推导图谱（`nodes.tsv` / `relations.tsv`）作底稿。

**第二步 · 模型写 `graph.json`（唯一需要判断的一步）**

读 `blocks.tsv`，把材料的论证结构写成 `/app/output/graph.json`：

```json
{"title": "…", "root": "N1",
 "nodes": [{"id": "N1", "parent": null, "kind": "title", "evidence_type": "fact",
            "locator": "manuscript.md#p1b1", "label": "≤24 字的要点",
            "quote": "从 blocks.tsv 的 text 里逐字复制的片段"}],
 "edges": [{"source": "N2", "target": "N3", "relation": "supports", "basis": "一句中文依据"}]}
```

硬约束（第三步会逐条校验，不满足就退回推导图谱）：

| 约束 | 值 |
|---|---|
| `root` | 只能 1 个根；其余节点必须带 `parent` |
| `locator` | **从 `blocks.tsv` 复制**，可加句子后缀 `s3`；编造定位会被拦下 |
| `quote` | 在材料里**逐字存在**（忽略空白）；允许短于原块，不许改字 |
| `label` | ≤24 字，写**判断或量值**而不是整句复制；`kind` 用 `title/section/method/claim/outcome/quantity/limitation/gap` |
| `relation` | 只在 `supports / depends / contrasts / conflicts / structural` 里选，且必须给 `basis` |
| 覆盖 | 每个 `#` 小节至少 1 个节点；量值单独成 `quantity` 节点，别只挂在方法句里 |

关系的判据要具体：`supports` 指证据支持结论；`depends` 指结论成立依赖另一条；`conflicts` 指两处数据/论断不能同时成立（例如孔隙率只降 0.3 个百分点却要解释 3 倍寿命提升）；`contrasts` 指对照或局限与主结论的张力。**没有依据就不要连边**——不加边比加错边得分高。

**第三步 · 校验 + 渲染（已在上面的命令里执行）**

`--verify-graph` 写 `graph_check.json`：`every_node_has_locator / quotes_verbatim / edges_have_types / edges_connected / has_typed_relations / all_passed`。
`all_passed=false` 时按 `problems` 逐条修 `graph.json` 再重跑；改不动就让它退回推导图谱，**不要手改 `nodes.tsv`**。

## 交付物

| 文件 | 内容 |
|---|---|
| `mindmap.mm` / `mindmap.opml` | 可再编辑的思维导图（FreeMind / OPML） |
| `mindmap.svg` / `mindmap.png` | 成图（SVG 可编辑；PNG 依赖本机渲染器，缺渲染器时如实写进 `result.json`） |
| `mindmap.md` | 缩进大纲（每行带 `locator` 与边类型） |
| `nodes.tsv` / `relations.tsv` | 结构化图数据：节点（含 `quote_sha256`）与有类型的边 |
| `blocks.tsv` | 第一阶段的块与定位（模型写图的唯一依据） |
| `graph_check.json` | 模型图的门禁结果（是否采用 `graph.json` 的依据） |
| `coverage.json` | 块覆盖率、节点类型分布、关系分布、图深 |
| `inventory.tsv` / `repro_manifest.json` | 六维文件元数据与复跑信息（`attach_meta.py` 产出） |

判定边界：**只用材料里有的东西** —— 每条结论带 `文件#定位`；材料没写的字段写「材料未说明」；做不到的写进 `gaps`，不写成已完成。
