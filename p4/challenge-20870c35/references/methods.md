# 方法与判据

## 本题的工作流程

1. 定听众与时长（题面给出则照用）：总时长 → 页数 → 每页秒数；把 `timing.json` 落盘。
2. 按金字塔结构组织：总论点 → 3-5 个支撑论点 → 每论点 1-3 页证据；每页写一句 takeaway。
3. 内容密度控制：每页一个 takeaway + ≤5 条支撑 + 至少一个可核对信息；把可省略细节挪到附录页。
4. 讲稿：每页 notes 写 30-60 秒口播要点 + 该页证据定位符。
5. 生成 pptx + 预览；输出 `outline.json` 与 timing 的对应表作为 content_map。
6. result.json：content_map（页→论点→证据）+ compliance_checks（页数/时长/密度）。

## 交付前自检清单

- 每页一个 takeaway
- 时长与页数匹配
- 密度未超限
- 证据可追溯
- 附录承载细节而非删事实

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
