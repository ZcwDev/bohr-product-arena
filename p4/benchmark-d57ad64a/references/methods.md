# 方法与判据

## 本题的工作流程

1. 定义检查项与判定规则：数字保留率、单位保留、引用双向一致、章节齐全、术语一致、禁用夸大词、模态未升级。每项给机械规则与阈值。
2. 定义输入格式：`cases.jsonl`（要求 + 材料 + 白板件）与 `predictions/`（被测产出）。
3. 用评分器 `scripts/writebench.py`：从案例契约推导检查项，逐项打分，输出 `score.json` + `failures.jsonl`（差异片段与触发规则）。

   ```bash
   python3 "$SKILL_DIR"/scripts/writebench.py spec      case.json -o spec.json
   python3 "$SKILL_DIR"/scripts/writebench.py selfcheck spec.json
   python3 "$SKILL_DIR"/scripts/writebench.py score     spec.json result.json \
       --blocks blocks.json --deliverable draft.md -o score.json --failures failures.jsonl
   python3 "$SKILL_DIR"/scripts/writebench.py report    score.json -o report.md
   python3 "$SKILL_DIR"/scripts/writebench.py calibrate a.json b.json -o calib.json
   ```

   退出码：`0` 全部已评分项通过；`1` 有失败项或不可评分项；`2` 用法/IO 错误。材料不足的检查记 `skip` 而不是 `pass`，且 `skip` 不进分母。
4. 容错：文件缺失/编码异常/空产出各计 0 并计数；不得因单例崩溃中断整批。
5. 内置示例（synthetic，标注）跑通并给出示例分数。
6. 写清适用范围、不判什么、如何加检查项。

## 交付前自检清单

- 评分器离线可跑
- 每项规则可机械判定
- 失败样例给差异与规则
- 异常不崩并计数
- 示例标注
- 扩展方式写清

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
