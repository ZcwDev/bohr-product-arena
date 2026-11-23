# 第四期公开作品索引

每个目录 = 一份可直接跑的 Skill：`SKILL.md`（单命令入口）+ `scripts/`（纯标准库实现）+ `RUBRIC.md`（评分表逐行映射，未覆盖处如实标注）。

| 题目 | 目录 | 入口 |
| --- | --- | --- |
| 多步骤数据分析 Agent：科研数据分析：给出结果，也保留计算依据 | [agent-6aefb2fd](agent-6aefb2fd/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --ou` |
| 文献检索 Agent 与自动迭代：科研检索：找到文献，也保留筛选依据 | [agent-7d0e6569](agent-7d0e6569/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| agent-d1ab67e5 | [agent-d1ab67e5](agent-d1ab67e5/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --out` |
| agent-f8fb8bda | [agent-f8fb8bda](agent-f8fb8bda/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input -` |
| ai-eccf0b86 | [ai-eccf0b86](ai-eccf0b86/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 浏览器与软件智能体 Benchmark：科研自动化：完成操作，也保留过程轨迹 | [benchmark-6428712b](benchmark-6428712b/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --out` |
| benchmark-a1d81ca4 | [benchmark-a1d81ca4](benchmark-a1d81ca4/) | `SKILL_DIR=${SKILL_DIR:-/skills/weekly-skill} python3 "$SKILL_DIR/scrip` |
| 学术写作 Benchmark 与评测工具：学术写作：完成产物，也保留来源 | [benchmark-d57ad64a](benchmark-d57ad64a/) | `python3 /skills/weekly-skill/scripts/writing_bench.py --input /input -` |
| 资料管理领域总课题：科研资料管理：整理文件，也保留版本关系 | [challenge-00e37955](challenge-00e37955/) | `python3 /skills/weekly-skill/scripts/library.py --mode all --input /in` |
| 科研选题与创新性评估：文献综述：综合证据，也说明边界 | [challenge-041f89c5](challenge-041f89c5/) | `python3 /skills/weekly-skill/scripts/topic_eval.py --input /input --ou` |
| 检索覆盖率与遗漏分析：科研检索：找到文献，也保留筛选依据 | [challenge-0467f5db](challenge-0467f5db/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 研究前沿与趋势分析：文献综述：综合证据，也说明边界 | [challenge-073af7d3](challenge-073af7d3/) | `python3 /skills/weekly-skill/scripts/trend_scan.py --input /input --ou` |
| 论文质量审查：论文质量审查：发现问题，也给出证据 | [challenge-0999879e](challenge-0999879e/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input -` |
| challenge-105b70ee | [challenge-105b70ee](challenge-105b70ee/) | `# 本 Skill 在评测环境里的安装位置（找不到就全局搜一下） SKILL_DIR=${SKILL_DIR:-/skills/weekly` |
| 科研文件智能检索：科研资料管理：整理文件，也保留版本关系 | [challenge-139745c7](challenge-139745c7/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --outpu` |
| challenge-142d91a2 | [challenge-142d91a2](challenge-142d91a2/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 文献智能问答：回答问题，也给出依据 | [challenge-15c85674](challenge-15c85674/) | `python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output ` |
| 论文逻辑与论证审查：论文质量审查：发现问题，也给出证据 | [challenge-16d4414d](challenge-16d4414d/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input -` |
| 科研数据图表生成与编辑：科研绘图：生成图稿，也保留可编辑来源 | [challenge-1bd90d8a](challenge-1bd90d8a/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --outp` |
| 最新文献发现与追踪：科研检索：找到文献，也保留筛选依据 | [challenge-1e85736f](challenge-1e85736f/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 学术汇报内容组织：学术汇报：组织页面，也保留证据映射 | [challenge-20870c35](challenge-20870c35/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --out` |
| 深度研究与文献综述领域总课题：文献综述：综合证据，也说明边界 | [challenge-27d794ce](challenge-27d794ce/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input -` |
| challenge-2815a418 | [challenge-2815a418](challenge-2815a418/) | `python3 /skills/weekly-skill/scripts/reviewreply.py --input /input --o` |
| 科研资料库与文件管理：科研资料管理：整理文件，也保留版本关系 | [challenge-2bfd9194](challenge-2bfd9194/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --outpu` |
| 文献筛选与智能排序：科研检索：找到文献，也保留筛选依据 | [challenge-2dd1f64c](challenge-2dd1f64c/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 实验方案可行性评估：实验方案设计：提出方案，也说明依据和风险 | [challenge-2e2c5820](challenge-2e2c5820/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| challenge-308a6fdc | [challenge-308a6fdc](challenge-308a6fdc/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| challenge-32c0210d | [challenge-32c0210d](challenge-32c0210d/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| 跨网站科研工作流自动化：科研自动化：完成操作，也保留过程轨迹 | [challenge-38dec22e](challenge-38dec22e/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --out` |
| 科研绘图领域总课题：科研绘图：生成图稿，也保留可编辑来源 | [challenge-38e0cc33](challenge-38e0cc33/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --outp` |
| 学术审查领域总课题：论文质量审查：发现问题，也给出证据 | [challenge-398dc732](challenge-398dc732/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input -` |
| 学术浏览器插件：科研自动化：完成操作，也保留过程轨迹 | [challenge-5047f57f](challenge-5047f57f/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --out` |
| 系统性文献综述：文献综述：综合证据，也说明边界 | [challenge-51977f9b](challenge-51977f9b/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 系统性文献检索与纳排筛选：科研检索：找到文献，也保留筛选依据 | [challenge-5b90f5e9](challenge-5b90f5e9/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 科研数据库查询与分析：科研数据分析：给出结果，也保留计算依据 | [challenge-5c56458d](challenge-5c56458d/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --ou` |
| 多数据库联合检索：科研检索：找到文献，也保留筛选依据 | [challenge-5c918ff0](challenge-5c918ff0/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| challenge-5d1ff744 | [challenge-5d1ff744](challenge-5d1ff744/) | `python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --o` |
| 引文追踪与关联文献发现：科研检索：找到文献，也保留筛选依据 | [challenge-60a8202d](challenge-60a8202d/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 文献与资料自动分类：科研资料管理：整理文件，也保留版本关系 | [challenge-666fefb0](challenge-666fefb0/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --outpu` |
| 科研资料收集与迁移：科研自动化：完成操作，也保留过程轨迹 | [challenge-71c94fb7](challenge-71c94fb7/) | `python3 /skills/weekly-skill/scripts/library.py --input /input --outpu` |
| 学术写作客户端：学术写作：完成产物，也保留来源 | [challenge-7b09c079](challenge-7b09c079/) | `python3 /skills/weekly-skill/scripts/revflow.py --work /app/work/revfl` |
| 实验方案比较与选择：实验方案设计：提出方案，也说明依据和风险 | [challenge-7be76e91](challenge-7be76e91/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| challenge-7c37626c | [challenge-7c37626c](challenge-7c37626c/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 论文结构与逻辑优化：学术写作：完成产物，也保留来源 | [challenge-8021aacb](challenge-8021aacb/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --out` |
| 跨语言文献检索：科研检索：找到文献，也保留筛选依据 | [challenge-802a7115](challenge-802a7115/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 科研知识库问答：科研资料管理：整理文件，也保留版本关系 | [challenge-80dd5ddc](challenge-80dd5ddc/) | `python3 /skills/weekly-skill/scripts/kb_qa.py --input /input --output ` |
| 实验结果解释与异常分析：实验方案设计：提出方案，也说明依据和风险 | [challenge-8354c69a](challenge-8354c69a/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| 生物机制图与通路图：科研绘图：生成图稿，也保留可编辑来源 | [challenge-83d4f050](challenge-83d4f050/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --outp` |
| challenge-9034312d | [challenge-9034312d](challenge-9034312d/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 科研软件插件与自动化领域总课题：科研自动化：完成操作，也保留过程轨迹 | [challenge-912f63bd](challenge-912f63bd/) | `python3 /skills/weekly-skill/scripts/flow_spec.py --input /input --out` |
| challenge-9313ad0a | [challenge-9313ad0a](challenge-9313ad0a/) | `python3 /skills/weekly-skill/scripts/mindmap.py --input /input --outpu` |
| 多文档比较与整合：科研阅读：回答问题，也给出依据 | [challenge-95061011](challenge-95061011/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input -` |
| 科研数据分析领域总课题：科研数据分析：给出结果，也保留计算依据 | [challenge-95620c56](challenge-95620c56/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --ou` |
| 科研数据可视化：科研数据分析：给出结果，也保留计算依据 | [challenge-9562cc8d](challenge-9562cc8d/) | `python3 /skills/weekly-skill/scripts/chart_render.py --input /input --` |
| challenge-96263d10 | [challenge-96263d10](challenge-96263d10/) | `python3 /skills/weekly-skill/scripts/template_adapter.py --input /inpu` |
| 科研示意图生成与编辑：科研绘图：生成图稿，也保留可编辑来源 | [challenge-98e5a474](challenge-98e5a474/) | `python3 /skills/weekly-skill/scripts/oneclick.py --input /input --outp` |
| 研究方法与统计审查：论文质量审查：发现问题，也给出证据 | [challenge-9e7302fd](challenge-9e7302fd/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --o` |
| 科研代码生成与执行：科研数据分析：给出结果，也保留计算依据 | [challenge-9ec929fe](challenge-9ec929fe/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --o` |
| 文献阅读领域总课题：科研阅读：回答问题，也给出依据 | [challenge-a028c71c](challenge-a028c71c/) | `python3 /skills/weekly-skill/scripts/readclient.py --work /app/output/` |
| 科研数据清洗与处理：科研数据分析：给出结果，也保留计算依据 | [challenge-a0e888fa](challenge-a0e888fa/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --ou` |
| 学术文献检索：科研检索：找到文献，也保留筛选依据 | [challenge-a2909a72](challenge-a2909a72/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 学术写作领域总课题：学术写作：完成产物，也保留来源 | [challenge-a2c6f23b](challenge-a2c6f23b/) | `python3 /skills/weekly-skill/scripts/revflow.py --work /app/output/wor` |
| 实验方案设计领域总课题：实验方案设计：提出方案，也说明依据和风险 | [challenge-a57a8549](challenge-a57a8549/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| challenge-a7eb6816 | [challenge-a7eb6816](challenge-a7eb6816/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 网页文献阅读与翻译：科研自动化：完成操作，也保留过程轨迹 | [challenge-aac59bef](challenge-aac59bef/) | `python3 /skills/weekly-skill/scripts/webread.py --input /input --outpu` |
| 专利与技术文献检索：科研检索：找到文献，也保留筛选依据 | [challenge-ad3e9357](challenge-ad3e9357/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 文献证据驱动写作：学术写作：完成产物，也保留来源 | [challenge-b810ef56](challenge-b810ef56/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| 文献研究质量评价：科研检索：找到文献，也保留筛选依据 | [challenge-bace1c46](challenge-bace1c46/) | `python3 /skills/weekly-skill/scripts/appraise_ledger.py --input /input` |
| 文献检索领域总课题：科研检索：找到文献，也保留筛选依据 | [challenge-baddb1f9](challenge-baddb1f9/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| challenge-bb17a294 | [challenge-bb17a294](challenge-bb17a294/) | `python3 /skills/weekly-skill/scripts/bilinguist.py --input /input --ou` |
| 阅读批注与知识沉淀：科研资料管理：整理文件，也保留版本关系 | [challenge-c050aaed](challenge-c050aaed/) | `python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input ` |
| 统计分析与结果解读：科研数据分析：给出结果，也保留计算依据 | [challenge-c0e1e220](challenge-c0e1e220/) | `python3 /skills/weekly-skill/scripts/stats_audit.py --input /input --o` |
| challenge-caca73b2 | [challenge-caca73b2](challenge-caca73b2/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 引用与事实核验：论文质量审查：发现问题，也给出证据 | [challenge-cbe96f01](challenge-cbe96f01/) | `python3 /skills/weekly-skill/scripts/verify_claims.py --input /input -` |
| 实验方案生成与优化：实验方案设计：提出方案，也说明依据和风险 | [challenge-cf1995b5](challenge-cf1995b5/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| challenge-d403dce6 | [challenge-d403dce6](challenge-d403dce6/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 论文复现与方法验证：科研阅读：回答问题，也给出依据 | [challenge-da62e8d4](challenge-da62e8d4/) | `python3 /skills/weekly-skill/scripts/docstruct.py --input /input --out` |
| 学术规范与合规检查：论文质量审查：发现问题，也给出证据 | [challenge-de9f0708](challenge-de9f0708/) | `python3 /skills/weekly-skill/scripts/quality_audit.py --input /input -` |
| challenge-e5e3be30 | [challenge-e5e3be30](challenge-e5e3be30/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| challenge-e60af428 | [challenge-e60af428](challenge-e60af428/) | `python3 /skills/weekly-skill/scripts/provenance_audit.py --input /inpu` |
| 科研问题驱动检索：科研检索：找到文献，也保留筛选依据 | [challenge-e7598a44](challenge-e7598a44/) | `python3 /skills/weekly-skill/scripts/screen_ledger.py --input /input -` |
| 文献综述生成：文献综述：综合证据，也说明边界 | [challenge-e97137c9](challenge-e97137c9/) | `python3 /skills/weekly-skill/scripts/review_matrix.py --input /input -` |
| 实验参数优化建议：实验方案设计：提出方案，也说明依据和风险 | [challenge-ec02b694](challenge-ec02b694/) | `python3 /skills/weekly-skill/scripts/plan_engine.py --input /input --o` |
| challenge-f35e4ca5 | [challenge-f35e4ca5](challenge-f35e4ca5/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| 文献数据清洗与标准化：科研阅读：回答问题，也给出依据 | [challenge-f4bca185](challenge-f4bca185/) | `python3 /skills/weekly-skill/scripts/data_clean.py --input /input --ou` |
| challenge-f730fc37 | [challenge-f730fc37](challenge-f730fc37/) | `python3 /skills/weekly-skill/scripts/run.py --input /input --output /a` |
| graphical-abstract-7030fff2 | [graphical-abstract-7030fff2](graphical-abstract-7030fff2/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 学术 PPT 生成与编辑：学术汇报：组织页面，也保留证据映射 | [ppt-6ad93034](ppt-6ad93034/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input` |
| 学术 PPT 模板与格式适配：学术汇报：组织页面，也保留证据映射 | [ppt-710fbc72](ppt-710fbc72/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input` |
| 学术 PPT领域总课题：学术汇报：组织页面，也保留证据映射 | [ppt-9508e25f](ppt-9508e25f/) | `python3 /skills/weekly-skill/scripts/deck_from_paper.py --input /input` |
| ppt-c7b80f0d | [ppt-c7b80f0d](ppt-c7b80f0d/) | `python3 /skills/weekly-skill/scripts/contract.py check /app/output/res` |
| 文献翻译：科研阅读：回答问题，也给出依据 | [product-arena-01-5a9928c1](product-arena-01-5a9928c1/) | `python3 /skills/weekly-skill/scripts/webread.py --input /input --outpu` |
| product-arena-02-7051d92d | [product-arena-02-7051d92d](product-arena-02-7051d92d/) | `python3 /skills/weekly-skill/scripts/polish.py --input /input --output` |
| product-arena-03-429cc14d | [product-arena-03-429cc14d](product-arena-03-429cc14d/) | `python3 /skills/weekly-skill/scripts/cite_manage.py --input /input --o` |
| product-arena-04-4b82ad21 | [product-arena-04-4b82ad21](product-arena-04-4b82ad21/) | `python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --` |
| product-arena-05-64faa4a2 | [product-arena-05-64faa4a2](product-arena-05-64faa4a2/) | `python3 /skills/weekly-skill/scripts/paper_writer.py --input /input --` |
| product-arena-06-6d8cf72b | [product-arena-06-6d8cf72b](product-arena-06-6d8cf72b/) | `python3 /skills/weekly-skill/scripts/annotate_notes.py --input /input ` |
| product-arena-07-865de7e4 | [product-arena-07-865de7e4](product-arena-07-865de7e4/) | `python3 /skills/weekly-skill/scripts/latex_check.py --input /input --o` |
| 文献文档解析：科研阅读：回答问题，也给出依据 | [product-arena-08-3027d350](product-arena-08-3027d350/) | `python3 /skills/weekly-skill/scripts/chartread.py --input /input --out` |
| product-arena-09-0e8befd4 | [product-arena-09-0e8befd4](product-arena-09-0e8befd4/) | `python3 /skills/weekly-skill/scripts/claim_audit.py --input /input --o` |
| product-arena-10-760d1a0b | [product-arena-10-760d1a0b](product-arena-10-760d1a0b/) | `python3 /skills/weekly-skill/scripts/mindmap.py --input /input --outpu` |
