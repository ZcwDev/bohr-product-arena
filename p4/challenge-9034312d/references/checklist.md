# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 部件与图上节点一一对应
- [ ] 参数带单位且来自材料
- [ ] 流向正确无歧义
- [ ] 步骤有判据
- [ ] 缺失规格如实标 unspecified
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
