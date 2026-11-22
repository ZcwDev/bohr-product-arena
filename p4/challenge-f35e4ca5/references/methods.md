# 方法与判据

## 本题的工作流程

1. 用 `pdf_text.py extract` 拿全文块与 `p<页>b<块>` 定位符；用 `pdf_text.py tables` 拿表格网格。
2. 筛出 `type=figure|table|caption`（或正则命中 `Fig./Table/图/表`）的块，为每张图/表建立一条 `figure_id` 记录：caption 原文、所在页、面板列表。
3. 坐标轴与单位：从 caption 与图内文本里抽 axis label/unit/scale（对数、断轴、归一化），单位缺失写 `not_reported`，不要补默认单位。
4. 数据点：优先从**表格**取数（表格是逐字可核对的），从图里只取能读到的标注值；读不到就写 `unreadable_from_figure` 并给出该面板的定位符。
5. 误差线与显著性：记录误差线含义（SD/SEM/CI）、n、p 值或星号，若 caption 未说明，写 `not_reported` 并在 `uncertainties` 里点名。
6. 多面板：按面板字母（A/B/C）拆分，保持每面板自己的坐标轴与注释，不要并成一个序列。
7. 导出 `result.json`（reading 契约），`claims` 里每条字段给 kind/evidence(locator+quote)。

## 交付前自检清单

- 每张图/表都有 caption 原文与定位符
- 数值与表格逐字一致（含单位与量级）
- 未报告项写 not_reported，未编造单位/n/显著性
- 多面板未串行

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
