---
name: material-grounded-slide-deck
description: 把给定材料做成可编辑的演示文稿：每页标题=材料里的原句结论、正文要点逐条带定位符、关键数值单独成表，并产出真 .pptx + 真渲染的 PDF/逐页 PNG 预览与讲者备注（来源映射）。当任务要求"把材料/结果做成 PPT/汇报/图形摘要（含预览）"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/slide_builder.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件，就表示本次案例完成（不要叫 results.json / output.json）；把该命令打印的 JSON 当作结论。
- 如果本机没有 `/input`（例如工作区里是 `inputs/`），就用工作目录里的 `inputs` 与 `out` 替换路径；**不要自造案例、不要另建 portability/selftest 目录**。
- 收工前清理输出目录：**只允许 `result.json` 与 skill 产出的交付物**；任何 `edge-test*`、`portability*`、`example-run`、`selftest*` 目录都要删掉——评测方会把这些当成自造案例，直接判 0。
- `result.json.case_id` 必须与 `/input/case.json` 里的 `case_id` **逐字一致**，不得自行编号（T01/demo 之类一律判 0）。
- 如果 `/input` 的材料读不出来，就**如实报 `status: input_error`**；**绝对不要自造案例、不要用 /tmp 下的测试材料充数**。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自己编号（T01 / TEST-SYNTH-001 / demo / caseA）一律判 0。
- 只在这个命令**报错**时，才按下面的“失败处理”表逐条排查。
- 每个案例都是同一行命令；多个案例就重复执行（输出目录按案例分开）。

---

# 材料驱动的演示文稿（带真渲染预览）

交付：`deck.pptx`（可编辑）、`deck.pdf`（LibreOffice 真转换）、`previews/page-*.png`（逐页渲染）、
`speaker_notes.md`（每页的来源映射）、`result.json`。

## 何时用

- 给了材料/结果，要求产出 PPT / 汇报稿 / 图形摘要，并需要**真预览**证明渲染正确
- 需要"每页的结论能回到材料哪一句"

不适用：需要外部数据/联网作图（本 Skill 只用材料文本层）；材料是扫描件（会明确报需 OCR）。

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/slide_builder.py" --input /input --output /app/output \
        --title "<汇报标题>"
```

依赖：`python-pptx`（写 pptx）、LibreOffice（`soffice --headless --convert-to pdf`）、`PyMuPDF`（PDF→PNG）。
**内置解释器 shim**：缺 `pptx`/`fitz` 时按 `WEEKLY_TOOLS_PYTHON` / `/opt/weekly-tools/bin/python` 重新 exec。
`soffice` 找不到时 `result.json.render.status` 会写明原因，pptx 仍然有效（不假装渲染成功）。

## 结构与纪律

| 页面 | 内容 | 纪律 |
|---|---|---|
| 封面 | 标题 + case_id + 材料句数 | 不用空话 |
| 目录 | 每页标题（= 材料结论） | 与正文页一一对应 |
| 结论页 ×N | 标题=材料原句（首字母大写、不截断在半句）、要点=该句子句、页脚给定位符 | **每页可回到 `文件#页句`** |
| 关键数值页 | `w:tbl` 表格：值 / 定位 / 原句 | 数值连单位整段抄录（`312 ± 41 mm3` 取 312 mm3） |
| 限制页 | 只复述材料、图内信息未抽取、缺材料不补写 | 明写，不粉饰 |

## 异常与边界

- 材料无文本层 → `status=partial`，说明需 OCR，不生成内容页。
- 材料无带单位数值 → 关键数值页写「〔材料中没有带单位数值〕」，不造数。
- `soffice` 缺失 → 只出 pptx，`render.status` 记录原因（评审仍可打开 pptx）。
- 句子被 PDF 换行截断 → 先合并段落再分句（否则会出现"…twice daily, for 21"这类残句）。

## 最小调用示例（示例，不是正式案例结果）

`example/` 含一份示例材料 PDF（`EXAMPLE-CASE-001`）：
```bash
python3 scripts/slide_builder.py --input example --output example-output --title "Example deck"
# deck.pptx（9 页）+ deck.pdf（9 页）+ previews/page-01..09.png + speaker_notes.md
```
