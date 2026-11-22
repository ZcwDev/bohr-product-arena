# 方法与判据

## 本题的工作流程

1. 抽论证链：论文的核心问题 → 方法 → 关键结果（含数值）→ 结论，形成 `outline.json`（每页一个结论句）。
2. 抽取可复用素材：图表/表格从论文取（记录来源页码，不重画数值）；数值逐字复制。
3. 写 deck spec：`title/bullets/notes/image/table`；每页 bullets ≤5 条、每条 ≤ 2 行；结果页必须带数值与条件。
4. 生成可编辑 pptx：优先用 `ooxml.py pptx`（无依赖），有 python-pptx 时也可用；讲稿备注写证据定位符。
5. 渲染预览：`render.py office` 优先；失败则 `preview.py slides` 出每页 PNG，绝不空口声称有预览。
6. 输出 result.json：content_map（每页结论→论文定位符）+ compliance_checks（页数、每页字数、图表编号对应）+ exceptions。

## 交付前自检清单

- 每页一个结论且与论文一致
- 数值逐字来自论文
- 图表编号对应原论文
- 备注含证据定位
- 可编辑 pptx + 真实预览
- 字数未超版面

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
