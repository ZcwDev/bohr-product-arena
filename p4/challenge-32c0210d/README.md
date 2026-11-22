# 学位论文与长文档写作（weekly-skill）

本目录是可直接安装的 Skill 包：`SKILL.md` 是入口契约，`scripts/` 是可运行脚本，
`references/` 是契约与方法说明，`example/` 是**合成示例**（不含正式案例结果）。

## 安装与调用

```bash
cp -r <this-dir> /skills/weekly-challenge-32c0210d
export SKILL_DIR=/skills/weekly-challenge-32c0210d

# 建索引 → 机检结果（带 --blocks 才会真的解析定位符）
python3 "$SKILL_DIR"/scripts/ingest.py build --case /input/case.json --dir /app/work/input -o /app/work/blocks.json
python3 "$SKILL_DIR"/scripts/contract.py check /app/output/result.json \
  --blocks /app/work/blocks.json --root /app --normalize
```

## 依赖

Python 3.9+ 标准库即可运行（本包实测于 3.9.6）。可选依赖见 SKILL.md 第 7 节。

## 局限

- 不判断科学正确性：本包只保证结果结构完整、定位可核验、未报告与冲突被如实标注。
- 定位符依赖 `pdf_text.py` 的输出；换用外部解析器时需自行保证定位符一致性。
- **原稿缺失时不会补写正文**：`ingest.py` 返回 `source_unavailable`，结果只能落在
  `not_reported` + 文件锚点上。这是有意为之——合成段落无法与真实原稿区分。
