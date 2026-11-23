# 第四期公开作品索引

每个目录 = 一份可直接跑的 Skill：`SKILL.md`（单命令入口）+ `scripts/`（纯标准库实现）+ `RUBRIC.md`（评分表逐行映射，未覆盖处如实标注）。

| 题目 | 目录 | 入口 |
| --- | --- | --- |
| 多步骤数据分析 Agent | [agent-6aefb2fd](agent-6aefb2fd/) | `—` |
| 文献检索 Agent | [agent-7d0e6569](agent-7d0e6569/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 科研软件操作 Agent | [agent-d1ab67e5](agent-d1ab67e5/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && p` |
| 深度研究 Agent | [agent-f8fb8bda](agent-f8fb8bda/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output ` |
| 当任务属于「Agent产品经理实习生招募」这一类 | [ai-eccf0b86](ai-eccf0b86/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 浏览器与软件智能体 Benchmark | [benchmark-6428712b](benchmark-6428712b/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && p` |
| 当任务属于「文献阅读 Benchmark 与评测工具」这一类 | [benchmark-a1d81ca4](benchmark-a1d81ca4/) | `python3 "$SKILL_DIR/scripts/run.py" --input /input --output /app/output && python3 /skills` |
| 把学术写作任务变成可判定的题集与评测工具 | [benchmark-d57ad64a](benchmark-d57ad64a/) | `python3 /skills/weekly-skill/scripts/writing_bench.py --input /input --output /app/output ` |
| 只读的科研资料库引擎 | [challenge-00e37955](challenge-00e37955/) | `python3 /skills/weekly-skill/scripts/library.py --mode all --input /input --output /app/ou` |
| 把候选选题按可算的三轴评估 | [challenge-041f89c5](challenge-041f89c5/) | `python3 /skills/weekly-skill/scripts/topic_eval.py --input /input --output /app/output && ` |
| 可复核的文献检索台账 | [challenge-0467f5db](challenge-0467f5db/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 把语料变成可数的趋势证据 | [challenge-073af7d3](challenge-073af7d3/) | `python3 /skills/weekly-skill/scripts/trend_scan.py --input /input --output /app/output && ` |
| 规则式的论文质量审查 | [challenge-0999879e](challenge-0999879e/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output ` |
| 当任务属于「文献结论与数据提取」这一类 | [challenge-105b70ee](challenge-105b70ee/) | `python3 "$SKILL_DIR"/scripts/run.py --input /input --output /app/output --work /app/work &` |
| 对科研文件集合做可复核的检索 | [challenge-139745c7](challenge-139745c7/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode` |
| 当任务属于「写作15｜开题报告与研究计划书」这一类 | [challenge-142d91a2](challenge-142d91a2/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 在给定知识库里检索并作答 | [challenge-15c85674](challenge-15c85674/) | `python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output /app/output && pytho` |
| 规则式质量审查（结构/交叉引用/引用一致性/绝对化表述/术语变体 + 统计表述），每条带定位、严 | [challenge-16d4414d](challenge-16d4414d/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output ` |
| 当任务属于「科研数据图表生成与编辑」这一类 | [challenge-1bd90d8a](challenge-1bd90d8a/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output && py` |
| 可复核的文献检索台账 | [challenge-1e85736f](challenge-1e85736f/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 学术汇报内容组织 | [challenge-20870c35](challenge-20870c35/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mo` |
| 把材料变成"证据矩阵 + 冲突表 + 空白清单 + 综述稿" | [challenge-27d794ce](challenge-27d794ce/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output ` |
| Build a revision package from a manuscript versi | [challenge-2815a418](challenge-2815a418/) | `python3 /skills/weekly-skill/scripts/reviewreply.py --input /input --output /app/output &&` |
| 只读的科研资料库管理 | [challenge-2bfd9194](challenge-2bfd9194/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode` |
| 可复核的文献检索台账 | [challenge-2dd1f64c](challenge-2dd1f64c/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 实验方案（可行性评估） | [challenge-2e2c5820](challenge-2e2c5820/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 把给定科研材料写成可编辑的学术稿件 | [challenge-308a6fdc](challenge-308a6fdc/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output && python3` |
| 当任务属于「学位论文与长文档写作」这一类 | [challenge-32c0210d](challenge-32c0210d/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output && python3` |
| 跨网站科研工作流自动化 | [challenge-38dec22e](challenge-38dec22e/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && p` |
| 科研绘图总课题 | [challenge-38e0cc33](challenge-38e0cc33/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output && py` |
| 规则式论文质量审查 | [challenge-398dc732](challenge-398dc732/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output ` |
| 学术浏览器插件 | [challenge-5047f57f](challenge-5047f57f/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && p` |
| 系统性文献综述 | [challenge-51977f9b](challenge-51977f9b/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 可复跑的系统性文献纳排台账 | [challenge-5b90f5e9](challenge-5b90f5e9/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 规则驱动的表格清洗 | [challenge-5c56458d](challenge-5c56458d/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output && ` |
| 可复核的文献检索台账 | [challenge-5c918ff0](challenge-5c918ff0/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 逐条审计报告中的观点是否被证据库支持 | [challenge-5d1ff744](challenge-5d1ff744/) | `python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --output /app/output` |
| 可复核的文献检索台账 | [challenge-60a8202d](challenge-60a8202d/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 把科研文件集合按规则分类（manuscript / figure / data / protoc | [challenge-666fefb0](challenge-666fefb0/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode` |
| 把资料收集与迁移做成只读可核对的清单 | [challenge-71c94fb7](challenge-71c94fb7/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --output /app/output --mode` |
| A working local academic-writing client as a CLI | [challenge-7b09c079](challenge-7b09c079/) | `python3 /skills/weekly-skill/scripts/revflow.py --work /app/work/revflow run --input /inpu` |
| 实验方案（比较与选择） | [challenge-7be76e91](challenge-7be76e91/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 当任务属于「公式解析与数学推理」这一类 | [challenge-7c37626c](challenge-7c37626c/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 论文结构与逻辑优化 | [challenge-8021aacb](challenge-8021aacb/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mo` |
| 可复核的文献检索台账 | [challenge-802a7115](challenge-802a7115/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 在给定知识库里检索并作答 | [challenge-80dd5ddc](challenge-80dd5ddc/) | `python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output /app/output && pytho` |
| 实验方案（结果解释与异常分析） | [challenge-8354c69a](challenge-8354c69a/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 生物机制图与通路图 | [challenge-83d4f050](challenge-83d4f050/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output && py` |
| 当任务属于「实验装置与技术路线图」这一类 | [challenge-9034312d](challenge-9034312d/) | `python3 "$SKILL_DIR"/scripts/figure.py inspect existing.svg            # 列出 node-/edge-/la` |
| 科研软件插件与自动化总课题 | [challenge-912f63bd](challenge-912f63bd/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --output /app/output && p` |
| Build an editable, source-grounded mind map from | [challenge-9313ad0a](challenge-9313ad0a/) | `python3 /skills/weekly-skill/scripts/mindmap.py --input /input --output /app/output && pyt` |
| 把多份文档整合成对比矩阵 | [challenge-95061011](challenge-95061011/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output ` |
| 规则驱动的表格清洗 | [challenge-95620c56](challenge-95620c56/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output && ` |
| 规则驱动的表格清洗 | [challenge-9562cc8d](challenge-9562cc8d/) | `—` |
| 当任务属于「学术格式与模板适配」这一类 | [challenge-96263d10](challenge-96263d10/) | `python3 /skills/weekly-skill/scripts/template_adapter.py --input /input --output /app/outp` |
| 当任务属于「科研示意图生成与编辑」这一类 | [challenge-98e5a474](challenge-98e5a474/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --output /app/output && py` |
| 规则式的方法与统计审查 | [challenge-9e7302fd](challenge-9e7302fd/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --output /app/output &&` |
| 规则式方法与统计审查 | [challenge-9ec929fe](challenge-9ec929fe/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --output /app/output &&` |
| 阅读客户端 | [challenge-a028c71c](challenge-a028c71c/) | `python3 /skills/weekly-skill/scripts/readclient.py --work /app/output/work/reading run --i` |
| 规则驱动的表格清洗 | [challenge-a0e888fa](challenge-a0e888fa/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output && ` |
| 可复核的文献检索台账 | [challenge-a2909a72](challenge-a2909a72/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 版本化改稿工作流 | [challenge-a2c6f23b](challenge-a2c6f23b/) | `python3 /skills/weekly-skill/scripts/revflow.py --work /app/output/work/revflow run --fres` |
| 实验方案（总课题） | [challenge-a57a8549](challenge-a57a8549/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 当任务属于「写作17｜科研成果转化写作」这一类 | [challenge-a7eb6816](challenge-a7eb6816/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 把网页/文档读成可核对的双语阅读单 | [challenge-aac59bef](challenge-aac59bef/) | `python3 /skills/weekly-skill/scripts/webread.py --input /input --output /app/output && pyt` |
| 可复核的文献检索台账 | [challenge-ad3e9357](challenge-ad3e9357/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 当任务属于「写作05｜文献证据驱动写作」这一类 | [challenge-b810ef56](challenge-b810ef56/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output --work /ap` |
| 文献研究质量评价的可复跑台账 | [challenge-bace1c46](challenge-bace1c46/) | `python3 /skills/weekly-skill/scripts/appraise_ledger.py --input /input --output /app/outpu` |
| 可复跑的系统性检索台账 | [challenge-baddb1f9](challenge-baddb1f9/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| A bilingual translation workstation that makes a | [challenge-bb17a294](challenge-bb17a294/) | `python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --output /app/output run` |
| 读一篇材料并留下可回查的批注 | [challenge-c050aaed](challenge-c050aaed/) | `python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input --output /app/output` |
| 规则式统计审查 | [challenge-c0e1e220](challenge-c0e1e220/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --output /app/output &&` |
| 当任务属于「写作10｜结果与讨论章节写作」这一类 | [challenge-caca73b2](challenge-caca73b2/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 把待核验的科研论断拆成可独立判定的子判断，为每条子判断定位最小充分证据（页码/区块/逐字引文）， | [challenge-cbe96f01](challenge-cbe96f01/) | `python3 /skills/weekly-skill/scripts/verify_claims.py --input /input --output /app/output ` |
| 实验方案（生成与优化） | [challenge-cf1995b5](challenge-cf1995b5/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 当任务属于「写作14｜基金申请书写作」这一类 | [challenge-d403dce6](challenge-d403dce6/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 论文复现与方法验证 | [challenge-da62e8d4](challenge-da62e8d4/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --output /app/output --mo` |
| 规则式规范审查 | [challenge-de9f0708](challenge-de9f0708/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input --output /app/output ` |
| 文献证据溯源与事实核验 | [challenge-e5e3be30](challenge-e5e3be30/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output && python3` |
| 按 Provenance Audit API v1 契约实现溯源审计服务并自带回归 | [challenge-e60af428](challenge-e60af428/) | `python3 /skills/weekly-skill/scripts/provenance_audit.py --input /input --output /app/outp` |
| 可复核的文献检索台账 | [challenge-e7598a44](challenge-e7598a44/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input --output /app/output ` |
| 把材料变成"证据矩阵 + 冲突表 + 空白清单 + 综述稿" | [challenge-e97137c9](challenge-e97137c9/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input --output /app/output ` |
| 实验方案（参数优化建议） | [challenge-ec02b694](challenge-ec02b694/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --output /app/output &&` |
| 当任务要求把科研图表当作带结构的数据源来解析 | [challenge-f35e4ca5](challenge-f35e4ca5/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output && python3` |
| 把杂乱文献数据整理成可复算的清洗台账 | [challenge-f4bca185](challenge-f4bca185/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --output /app/output && ` |
| 当任务属于「阅读客户端」这一类 | [challenge-f730fc37](challenge-f730fc37/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /app/output && python3` |
| 当任务属于「Graphical Abstract（图形摘要）」这一类 | [graphical-abstract-7030fff2](graphical-abstract-7030fff2/) | `python3 "$SKILL_DIR"/scripts/figure.py inspect existing.svg            # 列出 node-/edge-/la` |
| 学术 | [ppt-6ad93034](ppt-6ad93034/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/outpu` |
| 学术 | [ppt-710fbc72](ppt-710fbc72/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/outpu` |
| 把论文做成可编辑 deck | [ppt-9508e25f](ppt-9508e25f/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input --output /app/outpu` |
| 当任务属于「论文转学术 PPT」这一类 | [ppt-c7b80f0d](ppt-c7b80f0d/) | `作者型：agent 按 SKILL.md 写出 /app/output/result.json，脚本仅做机检与渲染` |
| 网页/文档双语阅读与翻译 | [product-arena-01-5a9928c1](product-arena-01-5a9928c1/) | `python3 /skills/weekly-skill/scripts/webread.py --input /input --output /app/output && pyt` |
| 学术语言润色 | [product-arena-02-7051d92d](product-arena-02-7051d92d/) | `python3 /skills/weekly-skill/scripts/polish.py --input /input --output /app/output && pyth` |
| 参考文献与引用管理 | [product-arena-03-429cc14d](product-arena-03-429cc14d/) | `python3 /skills/weekly-skill/scripts/cite_manage.py --input /input --output /app/output &&` |
| 写作08｜引言与相关工作写作 | [product-arena-04-4b82ad21](product-arena-04-4b82ad21/) | `python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output &` |
| 写作09｜方法与实验章节写作 | [product-arena-05-64faa4a2](product-arena-05-64faa4a2/) | `python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --output /app/output &` |
| 文献总结与结构化笔记 | [product-arena-06-6d8cf72b](product-arena-06-6d8cf72b/) | `python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input --output /app/output` |
| LaTeX 写作与编译 | [product-arena-07-865de7e4](product-arena-07-865de7e4/) | `python3 /skills/weekly-skill/scripts/latex_check.py --input /input --output /app/output &&` |
| 文献文档解析 | [product-arena-08-3027d350](product-arena-08-3027d350/) | `python3 /skills/weekly-skill/scripts/chartread.py --input /input --output /app/output && p` |
| Nanopublication | [product-arena-09-0e8befd4](product-arena-09-0e8befd4/) | `python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --output /app/output --` |
| 文献知识图谱构建 | [product-arena-10-760d1a0b](product-arena-10-760d1a0b/) | `python3 /skills/weekly-skill/scripts/mindmap.py --input /input --output /app/output --run-` |
