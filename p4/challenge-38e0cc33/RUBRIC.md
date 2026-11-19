# RUBRIC — 科研绘图领域总课题：科研绘图：生成图稿，也保留可编辑来源

`challenge-38e0cc33` · 公开链接：https://github.com/ZcwDev/bohr-product-arena/tree/main/p4/challenge-38e0cc33

本题按组织方评分表打分（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10，公开 GitHub 链接另加最多 5 分）。下表把每一行映射到我实际交付的文件，**没有覆盖的写「未覆盖」**。

| 评分维度 | 分值 | 我的对应产物 | 状态 |
| --- | --- | --- | --- |
| 实际效果 | 40 | `check_report.json`, `result.json` | 覆盖 |
| 使用增益 | 25 | — | 部分（有方法与自查，无独立 baseline 对照） |
| 创新增益 | 15 | `check_report.json`, `result.json` | 覆盖 |
| 可靠体验 | 10 | `SKILL.md`, `check_report.json` | 覆盖 |
| 生态贡献 | 10 | `SKILL.md` | 覆盖 |

## 入口与复跑

- 单命令入口：`python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output`
- 复跑需要的信息在 `repro_manifest.json`（命令行、python 版本、输入逐文件 sha256、输出逐文件 sha256）。
- 六维文件元数据在 `inventory.tsv`（类型 / 元数据 / 目录层级 / 重复 sha256 / 版本链 / 权限与来源，缺项显式写 `missing`）。

## 已知未覆盖 / 不适用

- 使用增益（25 分）要求与「不加载 skill 的基础 Agent」做对照；我只有本 skill 的产物与自查，
  **没有在同输入下跑 baseline 的对照数据**，因此这一行按「部分」记，不冒充已证明的增益。
- 创新增益（15 分）的「相对已有方案的边界」只能靠产物结构说明，没有第三方基线。
