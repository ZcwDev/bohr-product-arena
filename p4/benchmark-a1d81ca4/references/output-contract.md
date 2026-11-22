# 输出契约（reading 家族）

```json
{
 "case_id": "原样保留",
 "status": "answered|partial|insufficient_evidence|input_error",
 "result": "题目主结果，结构化字段按题目任务卡",
 "claims": "每条关键结论附 kind、evidence、source_id、location",
 "uncertainties": "未报告、无法判断、冲突和人工复核项"
}
```

非 `answered` / 非 `ok` 时必须在 `limitations` 里写清缺口与人工接管位置。
