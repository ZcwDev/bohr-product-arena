# 方法与判据

## 本题的工作流程

1. 把源数据整理成 `data.json`（categories + series[].values + errors + n），每个数来自表格定位符，不改值。
2. 选图型并写清理由（比较/分布/相关/构成）；给 `chart` spec。
3. 生成：`figure.py chart spec.json -o fig.svg --png fig.png`（matplotlib 可用时出 PNG/PDF；不可用则 SVG 兜底并在 warnings 说明）。
4. 标注完整：轴标签+单位、量级（是否对数/归一化）、图例与系列一一对应、误差线含义（SD/SEM/CI）、n、显著性标记含义。
5. 对照校验：脚本把 spec 里的数值 vs 图元（柱高/点坐标）反算比对，输出 `compliance_checks`；一致才交付。
6. 导出 svg+png+pdf（可编辑源保留），图注写出数据来源与统计方法。

## 交付前自检清单

- 每个图元可反算回源数据
- 单位/量级/误差线含义齐全
- 图例与系列一致
- 中文标注未乱码
- 可编辑源与位图同时交付

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
