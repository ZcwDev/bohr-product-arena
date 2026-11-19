# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每个图元可反算回源数据
- [ ] 单位/量级/误差线含义齐全
- [ ] 图例与系列一致
- [ ] 中文标注未乱码
- [ ] 可编辑源与位图同时交付
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
