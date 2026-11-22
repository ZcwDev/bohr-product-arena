# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 评分器可离线运行并给分
- [ ] 检查项可机械判定
- [ ] 失败样例含差异原因
- [ ] 异常不崩
- [ ] 示例标注为示例
- [ ] 限制与扩展方式写清
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
