# 方法与判据

## 本题的工作流程

1. 抽实体与关系：把材料里的对象、状态、作用（激活/抑制/结合/转运/修饰）抽成 `diagram.json` 的 nodes/edges，每条给定位符。
2. 关系方向核对：上下游不能颠倒，抑制必须与被抑制对象相连；不确定的关系标 `association` 并写 note，不硬判。
3. 布局：按通路层次分层（`layers`），跨层边用 `via` 微调曲率，避免边穿过节点。
4. 语义渲染：`figure.py diagram spec.json -o fig.svg --png fig.png`，图例必须解释每种箭头/颜色/线宽代表什么。
5. 导出：可编辑源 svg + `preview.py diagram` 的 PNG/PDF；分辨率 ≥200dpi；中文字体回退链写清。
6. 标注来源与版本：图中每个关键元素在 `content_map` 里给来源定位符；导出检查结果写 `compliance_checks`。

## 交付前自检清单

- 实体/关系均可回溯
- 箭头语义在图例里有定义
- 无材料外的基因/浓度/时间点/单位
- svg 可编辑且元素有名字
- PNG 预览真实渲染
- compliance_checks 记录导出检查

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
