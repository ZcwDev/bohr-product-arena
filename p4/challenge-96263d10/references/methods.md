# 方法与判据

## 本题的工作流程

1. 解析目标模板：从模板文件/题面抽 6-10 条硬规则（页面、字体层级、标题编号、图表题注、参考文献样式、行距），写成 `rules.json`（每条含出处）。
2. 解析源稿：抽出标题层级、图表、引用、普通段落，保存为可改的结构（不用正则直接全文替换）。
3. 逐条套用规则，每次改动写一条 `change_log`（rule/field/before/after/locator）。
4. 机械校验：写 `scripts/format_check.py` 检查可机械判定的规则（编号连续、题注位置、引用样式正则、层级顺序），输出 `compliance_checks`。
5. 内容守护：`text_checks.py numbers` + `terms` 保证套模板过程未改数字、单位、术语。
6. 导出可编辑件 + 渲染预览（`render.py office`），预览失败要在 `exceptions` 里如实写。

## 交付前自检清单

- 每条规则有出处
- 机械规则已脚本校验
- 科学内容零改动（数字/术语）
- 合规检查结果写入 result.json
- 预览或失败原因都在

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
