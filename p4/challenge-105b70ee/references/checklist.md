# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每个 field_id 恰好一条结果，无遗漏无多写
- [ ] 值与原文字面一致（含数字格式与单位）
- [ ] not_reported 未被写成 0 或默认值
- [ ] 冲突字段给两处定位符
- [ ] quote 能被 verify_quote 命中
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
