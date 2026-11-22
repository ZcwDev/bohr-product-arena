# 方法与判据

## 本题的工作流程

1. 读 `/input/claims-to-verify.json`，把每条断言登记 `claim_id`；断言是**待核验问题**，不是事实。
2. 用 `evidence.py split` 拆成原子断言，记录每原子的否定词、比较对象、显著性、适用条件（体系/剂量/时间点/人群）。
3. 对每原子断言定位候选来源（`evidence.py locate`），并读完整上下文（必要时读该段前后块与所在小节），不做片段拼接。
4. 判定：全部原子被同一来源逐字支持且条件一致 → `supported`；部分原子/条件不同/只在更弱限定下成立 → `partially_supported`；来源给出相反表述 → `contradicted`；材料未涉及或证据不足 → `insufficient_evidence`。
5. 边界规则：材料只是“没做这个实验”不能写成 `contradicted`，要写 `insufficient_evidence` 并说明是外推。
6. 多来源冲突：给出两处定位符 + 限定性判定（例如“在 A 体系成立、B 体系相反”），不要选边。
7. 输出 `result.json`（reading 契约），每条断言一个 verdict + evidence 列表 + reason。

## 交付前自检清单

- 每条断言都拆过原子
- verdict 只用四个允许值
- 否定/比较/条件未丢
- 未被实验反驳的写成 insufficient_evidence 而非 contradicted
- 冲突给两处定位符

## 定位符约定

- PDF：`p<页>b<块>`（块号在同一页内从 1 递增，由 `pdf_text.py` 生成）
- 纯文本：`<文件名>:<行号>` 或 `<文件名>:<段落号>`
- 代码/配置：`<文件>:<行号>`
- 引语核验：`evidence.py verify blocks.json quotes.json`，`exact=true` 才可作为逐字证据
