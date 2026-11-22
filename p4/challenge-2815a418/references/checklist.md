# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 审稿意见逐条编号且逐字引用
- [ ] 每条有分类与处置
- [ ] 未采纳给出了理由与替代
- [ ] 回应与改稿一一对应
- [ ] 无空泛回应
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize --contract /input/case.json` 无 problems
- [ ] `python3 $SKILL_DIR/scripts/consistency.py check /app/output/result.json --letter <回应信> --revised <改稿>` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `limitations` 或 `exceptions`
