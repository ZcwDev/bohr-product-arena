---
name: claim-evidence-audit
description: 逐条审计报告中的观点是否被证据库支持：把每条 claim 拆成内容单元（数值+单位、术语、中文二元组）与 corpus 的每个段做逐段比对，数值必须按 token 相等比较（"5" 不能靠子串匹配 "55"）；输出 supported/contradicted/insufficient 三态、可逐字核对的原段引用与简短 audit_note；被修订/撤稿的段落只能用于反驳，绝不用于支持；原引用对不上时明确指出并改用正确出处。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）；
  逐观点的交付物是 `audit.json` + `annotated_report.md`。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

# 科研报告逐观点证据核验

逐条审计报告中的观点是否被证据库支持：把每条 claim 拆成内容单元（数值+单位、术语、中文二元组）与 corpus 的每个段做逐段比对，数值必须按 token 相等比较（"5" 不能靠子串匹配 "55"）；输出 supported/contradicted/insufficient 三态、可逐字核对的原段引用与简短 audit_note；被修订/撤稿的段落只能用于反驳，绝不用于支持；原引用对不上时明确指出并改用正确出处。

| 文件 | 内容 |
| --- | --- |
| `audit.json` | `claims[]`：`claim_id` · `status`（三态）· `evidence[]`（doc_id#section_id + 原段引文 + 重合度）· `audit_note`（≤2000 字符）· 原引用问题 |
| `annotated_report.md` | 每条观点一行 `ID | status | [doc_id#section_id], ...`；无证据写 `无有效证据`；顺序与 `audit.json` 一致 |
| `check_report.json` | 机检：id 完整不重复 / 状态只取三值 / 引用都在证据库里 / note 非空 / 报告行与 JSON 一致 / 未把撤稿段落当支持 |
| `result.json` | `audit` 统计 · `gaps` · `limitations` · `verification` · `metadata_coverage` |

判定边界：**引用必须逐字来自证据库**（不改写、不拼接），判定只基于字面重合与数值**token 相等**，不做语义推测；
证据不足就写 insufficient 并说明差在哪，不引用不相关段落；撤稿/修订段落只用于反驳。
