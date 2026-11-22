# 方法与判据

## 本题的工作流程

1. 读 `/input/target-fields.json`：登记每个 `field_id` 的字段名、量纲、要求的条件维度；这是唯一的字段真值来源。
2. 为每个 field_id 在材料里做定位（`evidence.py locate`），候选人段落连同 `p<页>b<块>` 记下。
3. 抽值：scalar 保持原字面（不换算、不四舍五入），units 原样带出，statistic 注明（mean/median/HR/OR/AUC…），condition 抽出体系/剂量/时间点/n，comparator 抽出对照臂。
4. 语义核对：确认抽出的句子**确实**在陈述该字段（主谓宾与否定词、条件从句都要对），只字面命中而语义不符 → `source_conflict` 或继续找。
5. 状态：读到值 → `reported`；材料没写 → `not_reported`（不是 0）；两处材料互斥 → `source_conflict` 并给两处定位符；解析失败 → `extraction_failed` 并给出失败点。
6. 输出 `result.json`：`result.fields[]` 每项含 field_id/scalar/units/statistic/condition/comparator/status/paragraph_id/quote。
7. `claims` 记录字段级结论，`uncertainties` 记录未报告与冲突清单。

## 交付前自检清单

- 每个 field_id 恰好一条结果，无遗漏无多写
- 值与原文字面一致（含数字格式与单位）
- not_reported 未被写成 0 或默认值
- 冲突字段给两处定位符
- quote 能被 verify_quote 命中

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
