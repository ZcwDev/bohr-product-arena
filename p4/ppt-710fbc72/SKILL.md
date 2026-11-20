---
name: ppt_710fbc72-grounded-deck
description: 学术：三段式——脚本先把材料抽成"可引用要点 + 关键数值（带定位）"，再由你的模型写 slide 规格（标题 + 2–6 条要点，每条带 [n] 或 · 定位），最后脚本渲染 pptx/pdf/逐页预览并校验（标题长度 / 要点条数 / 单条长度 / 数值必须能在材料里找到）。所有要点可回到材料，不编内容。
---

## 立刻执行（不要探索）

第一步（脚本：抽取可引用要点与关键数值）：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --title "汇报标题" \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

第二步（你的模型写 slide 规格）：读 `speaker_notes.md` 里的要点与数值（每条都有定位），
写成 `/app/output/slides.json`：每页 `{"title": "...", "bullets": ["... · 文件#段号", "... [n]"]}`，
2–6 条要点/页、单条 ≤160 字符，**每个数值都必须来自材料**。

第三步（脚本按你的规格渲染 + 校验）：

```bash
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --slides /app/output/slides.json
python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/output --verify-slides /app/output/slides.json
```

- 校验写 `slides_check.json`：标题长度 / 每页要点数 / 单条长度 / **数值可回到材料**；不合格 exit 1，改完再跑。

---

# 学术

三段式：脚本抽要点与数值 → 模型写 slide 规格 → 脚本渲染并校验。所有要点可回到材料，不编内容。

| 文件 | 内容 |
| --- | --- |
| `deck.pptx` / `deck.pdf` | 可编辑演示文稿与渲染件（无 python-pptx 时走纯标准库 OOXML） |
| `previews/page-*.png` | 逐页预览图（有光栅化器时） |
| `speaker_notes.md` | 每页要点的材料定位，便于逐条核对 |
| `slides.json` | 你的模型写的 slide 规格（更好的一版） |
| `slides_check.json` | 机检：标题/要点数/单条长度/数值可回溯 |
| `result.json` · `inventory.tsv` · `repro_manifest.json` | 结果、六维元数据、复跑清单、运行状态 |

判定边界：**不编数字、不编出处** —— 数值必须能在材料里找到（校验会逐条比对）；缺证据的判断不进正文页；
校验不过就改规格，不要改校验阈值。
