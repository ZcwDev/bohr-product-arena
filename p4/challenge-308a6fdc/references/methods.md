# 方法与判据

## 本题的工作流程

1. 读 `/input/case.json` 的体裁、目标期刊/模板、字数与结构要求；先把结构骨架（章节 + 每节要点）落盘 `plan.json`。
2. 把材料里的可用事实抽成 `facts.jsonl`（数字、条件、术语 + 定位符）；没有定位符的内容不得进入正文。
3. 按骨架写正文：每段先给 topic sentence，再给证据句；数字与条件从 `facts.jsonl` 取，不重写量纲与符号。
4. 段落级自检：`text_checks.py numbers`（数字保留）、`terms`（术语一致）、`claims`（无引用句检查）。
5. 导出可编辑件（`ooxml.py docx` 或 Markdown + LaTeX），保留 `## 参考文献` 与正文引用编号一致。
6. 输出 `result.json`（writing 契约）：deliverable / preserved_facts / evidence_map / change_log。

## 交付前自检清单

- 结构满足题面体裁要求
- 数字与术语与材料一致
- 每个关键论断有来源或标为作者判断
- 可编辑件可打开
- change_log 记录改了什么与为什么

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
