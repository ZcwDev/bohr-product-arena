# 跨网站科研工作流自动化｜使用说明

> 本文件由 `p4_readme.py` 从 `SKILL.md`、`scripts/` 与目录结构自动生成，每一行都能在包内找到依据；
> 未在 `SKILL.md` 中声明的内容不臆造，直接标注「未声明」。

## 1. 用途

跨网站科研工作流自动化：把材料里的操作过程变成可核验规格——步骤逐条带 `文件#L行号` 定位、动作归类（打开/检索/筛选/选择/提取/导出/校验/上传）、站点与接口只登记材料原文出现的、材料引用的选择器原样登记，并逐条标注该步需要真实浏览器还是可无头执行；缺站点/缺选择器/缺接口一律写进 gaps，不写「已执行」。

## 2. 调用方式

```bash
python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 评测环境把本 Skill 装在 `/skills/weekly-skill/`，案例在 `/input/case.json`，结果写 `/app/output/result.json`。
- 本地没有 `/input` 时用包内 `example/`；**不要**用自造案例冒充正式结果。

## 3. 输入与输出

| 项 | 内容 |
|---|---|
| 输入 | `/input/case.json`（案例标识与材料清单）、`/input/task.md`、材料文件本体 |
| 主输出 | `/app/output/result.json`（含 `case_id`、`status`、主结果、证据定位、不确定性） |
| 可编辑件 | 由入口脚本按契约生成（SVG/PNG/PDF、docx/pptx、md 等），与 JSON 同目录 |
| 状态枚举 | 见 `result.json` 的 `status` 字段 |

## 4. 依赖与版本

- 运行时：Python 3（`python3`，纯标准库路径可离线跑）。
- 依赖：`fitz`（可选：缺失时自动走纯标准库回退路径，并在 `exceptions` 记录)。
- 脚本清单（4 个）：`_meta.py`、`_render.py`、`attach_meta.py`、`flow_spec.py`

## 5. 资源与可复用部分

- `references/`：未提供（本 Skill 的规则直接写在 `SKILL.md`）
- `example/`：未提供
- 脚本之间通过 `result.json` 的字段契约衔接，可单独复跑任一环节（`--verify-*` / `contract.py check`）。

## 6. 适用范围与限制

- 适用于 `SKILL.md` 描述的同类案例；超出该范围时按材料实际情况标注，不补造数据。
- 材料缺页、无文字层、扫描件等情形如实标注 `unable` / `not_reported`，不做 OCR 猜测。
- 未提供办公套件时走无依赖回退路径（纯标准库 SVG / OOXML 生成），并在 `exceptions` 写明用了哪条路径。

## 7. 失败处理

| 情况 | 处置 |
|---|---|
| 缺少输入或输入不可读 | `status=input_error`，在 `limitations` 写明缺什么 |
| 只能完成部分字段 | `status=partial`，逐项标出缺口而不是省略 |
| 结论无法从材料推出 | `status=insufficient_evidence` 或 `unable`，并给出已核过的范围 |
| 入口脚本报错 | 保留 `/app/work/` 中间产物，先落一份能过契约校验的部分结果 |

## 8. 自检与维护

- 交付前跑 `python3 scripts/contract.py check /app/output/result.json --normalize`（若包内提供该脚本）。
- 改动脚本后重跑 `example/` 一遍，确认 `result.json` 字段与 `SKILL.md` 契约一致。
- 维护信息：本目录与 `SKILL.md` 同版本发布；如需替换实现，保持入口命令与输出字段不变即可被平台直接加载。
