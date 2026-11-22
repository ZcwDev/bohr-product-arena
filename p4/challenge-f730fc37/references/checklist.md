# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 入口命令可离线运行且退出码为 0
- [ ] index 结果可被后续子命令复用
- [ ] show 输出与原文逐字一致
- [ ] 缺项如实标注
- [ ] 示例明确标为示例
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
