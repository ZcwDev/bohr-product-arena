---
name: latex-manuscript-check
description: LaTeX 写作与编译：对 .tex 做确定性校验并做安全修复——环境配对、\begin{document}/\end{document} 顺序、花括号平衡（只报告不改）、\cite 与 .bib 键相互解析、\ref 与 \label 对应、重复 label、未引用条目；有 TeX 引擎就真编译并附日志尾部，没有就如实写「未执行编译」；输入是 md 时生成可编译的 .tex 骨架。
---

## 立刻执行（不要探索）

单命令 skill。评测里请**直接执行下面这一行**，不要读脚本、不要重实现、不要先做仓库审计：

```bash
python3 /skills/weekly-skill/scripts/latex_check.py --input /input --output /app/output \
  && python3 /skills/weekly-skill/scripts/attach_meta.py --input /input --output /app/output
```

- 退出码 0 且输出目录里出现**名字正好是 `result.json`** 的文件即完成（不要叫 results.json）。
- **`case_id` 只能从 `/input/case.json` 逐字复制**；自编号一律判 0。
- 没有 `/input` 时用 `inputs/` 与 `out/`；**不要自造案例**；收工前删 `edge-test*`/`portability*`/`example-run`。

---

## 2.5 时间与请求预算（先保交付，再谈完美）

评测侧的硬约束：**64 次模型请求、900 秒、单次回答 16384 tokens**。按下面的检查点自控，任何时刻被打断都应有一份可交付结果：

| 检查点 | 时间/请求 | 必须已经落盘 |
|---|---|---|
| 读完契约 | ≤ 60s | 认清 `/input/case.json` 的 `case_id` 与材料清单 |
| 第一版完整结果 | **≤ 420s（≈ 24 次请求）** | **`/app/output/result.json`**，契约字段齐全，状态允许「部分完成」 |
| 补漏与校对 | 420–780s | 覆盖写 `result.json`，`change_log`/`uncertainties` 只增不删 |
| 收尾 | 剩下的时间 | 不再重写，只补 `limitations` 与导出件 |


规则：

1. **先落一版能过契约校验的结果，再改进**；不允许把「还在读材料」当成完成态。
2. 每次覆盖 `result.json` 前先备份 `/app/work/result.prev.json`——磁盘上任何时刻都要有一份可用的。
3. 值不值得再花一次请求，用这个判据：*这次调用能不能让某个字段变成「有据可查的值」？* 不能就停手。
4. 大材料不要整篇塞进上下文：先建索引（定位符），只取与当前字段相关的块。
5. 到 780s 还没收尾，就**停止改进、只做导出与自检**：超时会被判成失败，已落盘的部分结果才计分。
6. **单次回答保持简短**（≤ 300 字、≤ 2000 tokens）：评测里主要耗时是模型往返，不是脚本；不要复述文件内容、不要每步都总结，能一次 `python3 <脚本>` 解决的就不要拆成多轮对话。

# 写作13｜LaTeX 写作与编译

LaTeX 写作与编译：对 .tex 做确定性校验并做安全修复——环境配对、`\begin{document}`/`\end{document}` 顺序、花括号平衡（只报告不改）、`\cite` 与 .bib 键相互解析、`\ref` 与 `\label` 对应、重复 label、未引用条目；有 TeX 引擎就真编译并附日志尾部，没有就如实写「未执行编译」；输入是 md 时生成可编译的 .tex 骨架。

| 文件 | 内容 |
| --- | --- |
| `main.tex` | 校验/修复后的稿件（只做安全修复：文末补 `\end{document}`；花括号问题只报告） |
| `latex_report.md` | 按类别列出问题，每条带行号；含编译状态与边界说明 |
| `check_report.json` | 机检：环境配对 / 引用可解析 / ref 有 label / 文档体存在 / 编译状态已记录；含环境清单与编译日志尾部 |
| `main.pdf` | 仅当镜像里有 TeX 引擎且编译成功时产出，否则不出现（不伪造） |
| `inventory.tsv` · `repro_manifest.json` | 六维文件元数据与复跑清单 |

判定边界：**不替作者改数学** —— 花括号不平衡、`.bib` 未引用条目只报告不改；没有 TeX 引擎时不声称产出 PDF；
`all_passed` 只表示"校验器与报告完整"，稿件里的引用缺失属于发现项，会被逐条列出。
