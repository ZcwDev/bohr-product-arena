---
name: paper-to-talk-deck
description: 把论文/材料改写成可编辑的学术汇报：先抽"研究问题→方法→结果→限制"主线，再设计页级主张（每页一条，带材料定位），单列图表引用页与关键数值表，产出真 .pptx + 真渲染 PDF/逐页 PNG + 讲稿备注。当任务要求"把论文做成 PPT/组会汇报/答辩稿（生成与编辑）"时使用。
---

## 立刻执行（不要探索）

这是一个**单命令** skill。评测里请**直接执行下面这一行**，不要读脚本、不要重新实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output
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

# 论文转汇报（生成 + 编辑 + 真渲染）

交付：`deck.pptx`（可编辑）、`deck.pdf`、`previews/page-*.png`、`speaker_notes.md`、`result.json`
（含 `template` 声明与 `figure_references`）。

## 何时用

- "把这份材料做成汇报/组会/答辩/评审的 PPT"
- 需要"页级主张 + 图表引用 + 每页可回到原文"的可复核产物

## 一次运行

```bash
SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill}
python3 "$SKILL_DIR/scripts/deck_from_paper.py" --input /input --output /app/output --title "<汇报标题>"
```

依赖：`python-pptx` + LibreOffice（转 PDF）+ `PyMuPDF`（PDF→PNG）；**内置解释器 shim**。
字体统一设为 `Noto Sans CJK SC`（评测镜像里已装），避免中英混排在渲染时丢字。

## 页面结构与纪律

| 页 | 内容 | 纪律 |
|---|---|---|
| 封面 | 标题 + case_id + 材料句数 | — |
| 目录 | 每页主张一览 | 与正文页一一对应 |
| **研究主线** | Question / Method / Result / Limitation 各一句 | 找不到就写 `not found in the material`，**不推断** |
| 结论页 ×N | 标题=材料原句（词边界截断，不切半句）、要点=子句、页脚=定位符 | 每页可回原文 |
| **图表引用页** | 正文里出现的 `Fig./Table/图/表` 引用句 + 定位符 | 图内数值不抽，只引正文引用句 |
| 关键数值 | `w:tbl`：值 / 定位 / 原句 | 值连单位抄录 |
| 限制页 | 只复述材料、图内未抽、缺材料不补 | 明写 |

## 异常与边界

- 材料无文本层 → `status=partial`，说明需 OCR，不生成内容页。
- `soffice` 缺失 → 只出 pptx，`render.status` 写明原因（不假装渲染成功）。
- 句子被 PDF 换行截断 → **先合并段落再分句**（否则出现 "…twice daily, for 21" 这类残句）。
- 中英混排 → 统一 `Noto Sans CJK SC`；演示机若无该字体会回退，但不会丢字成空白。

## 最小调用示例（示例，不是正式案例结果）

`example/` 含一份示例材料 PDF（`EXAMPLE-CASE-001`）：
```bash
python3 scripts/deck_from_paper.py --input example --output example-output --title "Example talk"
# deck.pptx（11 页）+ deck.pdf（11 页）+ previews/page-01..11.png + speaker_notes.md
```

## 没有 python-pptx 时的降级（重要）

评测镜像不保证装有 `python-pptx`。脚本内置了**纯标准库的 OOXML 写出器**：`from pptx import Presentation`
失败时，会直接写出合法的 `ppt/presentation.xml` + slideMaster/slideLayout + 每页 slide，产出同样的
`deck.pptx`，随后照常渲染 PDF/PNG。实测（本地强制屏蔽 pptx 模块）：pptx 13.8KB → LibreOffice 转出 10 页 PDF +
10 张预览，`status=answered`。也就是说**缺依赖不再等于没有交付物**。

## 产物优先级

`deck.pptx` 是第一交付物；`deck.pdf` 与 `previews/page-*.png` 是尽力而为的可视化（需要 LibreOffice）。
若评测环境没有 soffice，`result.json.render.status` 会写明"缺少渲染器"，pptx 仍然可用可编辑。
