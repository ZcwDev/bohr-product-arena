# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 每条断言都拆过原子
- [ ] verdict 只用四个允许值
- [ ] 否定/比较/条件未丢
- [ ] 未被实验反驳的写成 insufficient_evidence 而非 contradicted
- [ ] 冲突给两处定位符
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
