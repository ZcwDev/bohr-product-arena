# 最小接口示例

`example_input.json` 是组织方题面包中的 `inputs/example-input.json` 的逐字副本；此处只作为输入与调用接口示例，不含固定案例答案。

```bash
export SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/run_case.py" "$SKILL_DIR/example/example_input.json" \
  --work-dir /app/work --output /app/output/result.json
python3 "$SKILL_DIR/scripts/contract.py" check /app/output/result.json \
  --family reading --contract /app/work/output_contract.json
```

示意输出的**字段形态**如下，不是提前提供的科学答案：

```json
{
  "case_id": "与本次输入原样一致",
  "status": "由实际材料和复核完整度决定",
  "result": {"summary": "本次实际覆盖情况"},
  "claims": [{"kind": "实际结论类型", "evidence": {"locator": "实际位置", "quote": "实际短引文或像素证据说明"}, "source_id": "实际来源", "location": "实际位置"}],
  "uncertainties": [{"status": "not_reported", "reason": "没有条目时说明原因，不使用空数组"}]
}
```

真实运行还会写 `limitations/sources/change_log/warnings/runtime/contract_check` 和 `tables.json`。状态、字段值、条目数量与输入材料以本次命令的真实输出为准。
