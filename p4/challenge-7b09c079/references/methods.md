# 方法与判据

## 本题的工作流程

1. 定义 CLI：`python3 scripts/writer.py new|check|fix|export`（标准库实现），输入是 `spec.json`（结构 + 模板）+ 材料目录。
2. new：按 spec 生成骨架稿（章节、占位、每节要写什么的提示），不编内容。
3. check：跑结构/术语/数字/引用四项检查，输出 `report.json`（problem/locator/suggestion）。
4. fix：只做安全的机械修复（编号连续、术语统一、引用格式），每次修复写日志；语义问题只报不改。
5. export：导出 docx/markdown（`ooxml.py`），保留标题层级与引用。
6. SKILL.md 写明契约、示例、局限（不做科学判断）。

## 交付前自检清单

- 四个子命令可离线运行
- check 报告含定位与建议
- fix 仅做机械修复且留日志
- 导出件可打开
- 示例标注为示例

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
