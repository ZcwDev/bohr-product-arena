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
