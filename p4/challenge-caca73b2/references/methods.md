# 方法与判据

## 本题的工作流程

1. 从材料抽出结果条目：指标、数值、单位、统计量（p/CI/效应量）、n、条件、对应图表编号。
2. 写 Results：按图表顺序或问题顺序陈述，每条结果一句事实句 + 引用图表编号；不写机制解释。
3. 写 Discussion：按 (a) 主要发现复述 (b) 与既有工作比较 (c) 机制解释（明确标 inference）(d) 限制 (e) 结论 五段式。
4. 解释语句前缀标注：`We infer/This suggests`（推断）vs `We observed`（观察），机械可查。
5. 对照自检：Results 段不得出现解释性词（suggest/indicate/because）；Discussion 的每个比较都要有来源。
6. 输出 result.json：deliverable 含两章，evidence_map 把每条结果指到图表/表格定位符。

## 交付前自检清单

- Results 无解释句
- 每条结果带统计量与条件
- 解释句已标注推断
- 比较有来源
- 图表编号与实际一致

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
