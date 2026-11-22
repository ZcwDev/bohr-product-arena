# 写作10｜结果与讨论章节写作（weekly-skill）

本目录是可直接安装的 Skill 包：`SKILL.md` 是入口契约，`scripts/` 是可运行脚本，
`references/` 是契约与方法说明，`example/` 是**合成示例**（不含正式案例结果）。

## 安装与调用

```bash
cp -r <this-dir> /skills/weekly-challenge-caca73b2
cat /skills/weekly-challenge-caca73b2/SKILL.md | head -40
python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize
```

## 依赖

只有标准库也可运行；可选依赖见 SKILL.md 第 7 节。

## 局限

- 不判断科学正确性：本包只保证结果结构完整、定位可核验、未报告与冲突被如实标注。
- 定位符依赖 `pdf_text.py` 的输出；换用外部解析器时需自行保证定位符一致性。
