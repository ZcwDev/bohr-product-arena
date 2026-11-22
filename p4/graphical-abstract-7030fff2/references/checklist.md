# 交付前自检（逐条打勾，未过的不许当成完成）

- [ ] 核心信息唯一且 ≤20 词
- [ ] 三段式齐全
- [ ] 图内文字 ≤60 词
- [ ] 尺寸/分辨率/字体实测合规
- [ ] 配色不依赖红绿对立
- [ ] 每个元素可回溯
- [ ] `python3 $SKILL_DIR/scripts/contract.py check /app/output/result.json --normalize` 无 problems
- [ ] 声称存在的文件在 `ls` 里看得到
- [ ] 未报告/冲突/无法判断的项都写在 `uncertainties` 或 `exceptions`
