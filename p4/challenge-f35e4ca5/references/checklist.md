# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每张图/表都有 caption 原文与定位符
- [ ] 数值与表格逐字一致（含单位与量级）
- [ ] 未报告项写 not_reported，未编造单位/n/显著性
- [ ] 多面板未串行
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
