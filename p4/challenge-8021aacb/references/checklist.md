# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每个诊断有 locator
- [ ] 事实零改动
- [ ] 引用重排后仍正确
- [ ] 桥梁句无新信息
- [ ] 改前/改后可比
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
