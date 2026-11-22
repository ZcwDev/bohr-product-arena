# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每条规则有出处
- [ ] 机械规则已脚本校验
- [ ] 科学内容零改动（数字/术语）
- [ ] 合规检查结果写入 result.json
- [ ] 预览或失败原因都在
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
