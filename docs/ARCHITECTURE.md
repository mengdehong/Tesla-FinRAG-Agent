
## 项目架构图

以下为 Tesla FinRAG Agent 系统的整体架构及数据流向图，展示了用户交互、编排规划、数据摄入、核心 RAG 引擎及底层存储与模型层之间的交互关系：

```text
========================================================================================
                               用户交互层 (Presentation)
========================================================================================
           [ Streamlit Demo UI ]                 [ CLI (__main__.py) ]
========================================================================================

    +-------------------+   ==================================================  +-------------------+
    |                   |   |         智能体编排与规划层 (Orchestration)         |  |                   |
    | 评估与可观测性层    |   ==================================================  | 全局运行时与配置层  |
    | (Observability &  |   | [ 问答智能体 ] --------> [ 查询规划器 ]            |  | (Context & Config)|
    |   Evaluation)     |   |       |                    |                   |  |                   |
    |                   |   |       v                    v                   |  |  [ settings.py ]  |
    | [ 评测工作台 ]      |   | [ Prompt管理(guidance) ] [ 多语言检测(i18n) ]      |  |  [ runtime.py  ]  |
    | (workbench.py)    |   |               |                                |  |                   |
    |                   |   |               v                                |  |                   |
    | [ 全链路 Trace ]    |   |   [ 基础服务协调与工具调度 (services.py) ]          |  |                   |
    | (tracing.py)      |   ==================================================  +-------------------+
    +-------------------+           |                                 |
            |                       |                                 | (摄入指令 / 调度)
            |                       v (调用)                          |
            |               ==============================      ======================================
            |               RAG 核心计算引擎 (Engines)            可靠数据摄入流水线 (Ingestion)   
            |               ==============================      ======================================
            |               [ 混合检索 (Hybrid)        ]      [ 状态清单管理 (manifest/state)  ]
            |               [ 指标计算 (Calculator)    ]      [ 原始财报 (PDF/XBRL)            ]
            |               [ 术语消岐 (Concepts)      ]      [ 表文分离 (Narrative/Tables)    ]
            |               [ 答案组装 (Composer)      ]      [ 语义防断裂分块 (Chunker)       ]
            |               [ 证据溯源 (Evidence)      ]      [ 向量与倒排索引提取             ]
            |               ==============================      ======================================
            v                       |                                 |
                                    +-----------------+---------------+
                                                      | (读/写)
                                                      v
                    ====================================================================
                        底层存储 (Storage) : [ LanceDB 向量库 ] | [ Lexical 内存词汇库 ]
                    ====================================================================
                                                      |
                                                      v
                    ====================================================================
                       大模型基础设施接口 (Infrastructure) : [ LLM Provider (Ollama等) ]
                    ====================================================================

```

## 项目文件组织

```
.
|-- README.md                                    
|-- app.py                                       # Streamlit 演示 UI 应用的入口文件
|-- docs                                         
|   |-- DECISION.md                              # 架构决策记录 (ADR)，记录关键的技术选型和决策
|   |-- DELIVERY.md                              # 交付总结报告，记录项目交付状态和成果
|   |-- PROJECT.md                               # 项目需求、规格和功能说明文档
|   `-- research                                 # 调研和实验记录文档目录
|       |-- 01-ProjectInitResearch.md            # 项目初期技术探索与基础模型调研记录
|       |-- 02-AgentsImplPlan.md                 # 智能体实现方案和技术路线计划
|       |-- 03-AccuracyOptimizationPlan.md       # RAG 系统准确率优化计划和技术方案
|       `-- 04-DevVsAgenticBenchmark-Qwen2.5-1.5B.md   # Base模型基准测试及对比评测记录
|-- pyproject.toml                               # Python 项目依赖和核心构建配置文件
|-- scripts                                      # 辅助独立脚本工具目录
|   |-- download_pdf.py                             
|   `-- download_xbrl.py                         
|-- src                                          
|   `-- tesla_finrag                             
|       |-- __init__.py
|       |-- __main__.py                          # 系统命令行执行主入口 (CLI)
|       |-- agent                                # 问答智能体相关模块，金融问答主智能体的编排与核心逻辑
|       |   `-- financial_qa_agent.py            
|       |-- answer                               # 答案生成、合成与输出格式化模块
|       |   `-- composer.py                        
|       |-- calculation                          # 财务指标计算引擎模块
|       |   `-- calculator.py                      
|       |-- concepts                             # 金融术语和财报概念对齐模块 (消歧义)
|       |   |-- catalog.py                         
|       |   `-- resolver.py                        
|       |-- evaluation                           # Q&A准确性评估与评测工具组件
|       |   |-- __main__.py                        
|       |   |-- answer_rendering.py                
|       |   |-- models.py                          
|       |   |-- runner.py                          
|       |   `-- workbench.py                       
|       |-- evidence                             # 证据收集与溯源引用追踪模块
|       |   `-- linker.py                             
|       |-- guidance.py                          # 基于 LLM 的系统级 Prompt 集中注册与控制
|       |-- i18n.py                              # 多语言服务模块 (主要支持中/英语境分析和回答)
|       |-- ingestion                            # 知识库构建：数据摄入、清洗和特征提取模块
|       |   |-- analysis.py                         # 对财报文档的篇章和逻辑结构化分析
|       |   |-- index_segmentation.py               # 分块切片机制，处理长文本 Index 级别的切分
|       |   |-- manifest.py                         # 摄入数据清单管理，定义语料批次状态
|       |   |-- narrative.py                        # 对文档中的叙事段落 (MD&A) 等提取处理
|       |   |-- pipeline.py                         # 连串解析处理、建立索引的离线主流水线
|       |   |-- source_adapter.py                   # 异构原始文件来源(如PDF/JSON)的标准化适配层
|       |   |-- state.py                            # 摄入管道的中间执行状态机管理
|       |   |-- tables.py                           # 财务表格解析提取与Markdown/结构化防丢帧转换
|       |   |-- validation.py                       # 文本解析质量校验与格式准入断言规则
|       |   |-- writers.py                          # 解析数据流落地存储接口封装
|       |   `-- xbrl.py                             # 基于 XBRL 结构化数据的特定格式处理与加载
|       |-- logging_config.py                    # 全局日志记录级别与格式化配置中心
|       |-- models.py                            # 定义贯穿系统的公共领域数据模型规范 (Pydantic / Dataclass)
|       |-- planning                             # (Sub-Agent) 解决复杂多步问题的查询规划模块
|       |   |-- llm_query_planner.py                # 借助 LLM 把宏观提问拆解为具体子查询搜索的实现
|       |   `-- query_planner.py                    # 查询拆解规划的通用接口和抽象定义
|       |-- provider.py                          # 对接底层不同大语言模型的协议适配与服务提供接口 (LLM Provider)
|       |-- repositories.py                      # 对各类数据存取资源的封装层 (通用 Repository 模式)
|       |-- retrieval                            # 融合知识召回与相关度排序模块 (RAG 核心检索)
|       |   |-- hybrid.py                           # 向量+词法的混合检索调度实现与分数融合
|       |   |-- in_memory.py                        # 内存级别的短生命周期检索器支持 (主要用于测试)
|       |   |-- lancedb_store.py                    # 基于 LanceDB 持久化向量数据库的读写与维护封装
|       |   |-- lexical.py                          # 对话历史和词法属性 (如 BM25 相关) 检索逻辑
|       |   `-- vector.py                        # 标准向量搜索接口及基本查询构建逻辑
|       |-- runtime.py                              # 全局运行时上下文与依赖倒置注入管理容器
|       |-- services.py                             # 组合编排下游调用的通用高阶业务服务 API 实现
|       |-- settings.py                             # 基于环境变量、配置文件的系统核心参数配置管理器
|       `-- tracing.py                              # 请求追踪与中间件 (可观测引擎，输出调优数据)
|-- tests                                        # 测试套件总目录 (包含单元测试、端到端测试)
|   |-- conftest.py                              # pytest 环境中的核心共用 fixtures (预构建上下文、mock 数据等)
|   |-- integration                              # 集成环境与完整端到端自动化测试
|   |   |-- test_e2e_complex_questions.py           # 模拟真实高深金融计算/对比类问答集成测试
|   |   |-- test_ingestion_integration.py           # 从生肉 PDF 到 LanceDB 落库建立索引的全链路测试
|   |   `-- test_pipeline_integration.py            # 摄入与召回合并执行的组合流水线功能集成评估
|   |-- test_agentic_components.py               # 验证不同Agent行为交互和工具调用的正确性测试
|   |-- test_answer_rendering.py                 # 答案组装模块正确生成 Markdown/表格等结构的测试
|   |-- test_bootstrap.py                        # 系统初始化启动、加载流程与状态转移功能测试
|   |-- test_calculator.py                       # 金融计算公式(如毛利率)在各种极限值下计算逻辑单元测试
|   |-- test_composer_intent_routing.py          # Composer 是否依照意图返回相应的模板引擎断言测试
|   |-- test_evaluation.py                       # 验证项目自研评估组件自身逻辑是否正常的测试
|   |-- test_i18n.py                             # 验证国际化多语言自动检测/动态切换是否符合预期
|   |-- test_ingestion.py                        # 文件摄入子模块 (如分段算法、异常文件读取等) 的独立测试
|   |-- test_interfaces.py                       # 业务接口与底层 Adapter 隔离及出入参正确性验证
|   |-- test_lancedb_retrieval.py                # LanceDB 中不同余弦相似度及 TopK 限制等召回功能的测试
|   |-- test_lancedb_store.py                    # 测试向量落库持久化、异常恢复和查询语句编译功能 
|   |-- test_lexical.py                          # 构建倒排与基于词法精准匹配算法的查询有效性验证
|   |-- test_models.py                           # 验证 Pydantic Model 反序列化、结构合法性和边界检查
|   |-- test_period_aware.py                     # 对齐年度/季度等财报时间段周期推演判断能力的逻辑测试
|   |-- test_phase_c.py                          # C阶段相关特定功能的预埋断言验证案例集
|   |-- test_planner_intent.py                   # LLM 意图规划器准确拆解并转换搜索指令验证
|   |-- test_provider.py                         # LLM 工具网关、Token 计算与 Provider Mock 调用测试
|   |-- test_runtime.py                          # 运行时上下文缓存、依赖获取及单例模式保证测试
|   |-- test_settings.py                         # 服务启动环境变量解析、参数覆盖优先级检查测试
|   |-- test_smoke.py                            # 最基础的冒烟测试，用于自动化CI核心生命体征检查
|   `-- test_workspace.py                        # 验证实验工作区域以及周边辅助机制是否正确挂载测试
```

## 技术栈清单 (Technology Stack)


| 领域 (Layer) | 核心技术组件 (Technologies) | 主要职责 (Role) |
| :--- | :--- | :--- |
| **基础语言与框架** | Python 3.12+, Pydantic, Streamlit | 核心开发语言、强类型领域数据建模与参数校验、交互式演示前端界面 |
| **大模型层 (本地化)** | Ollama, Qwen2.5 (1.5B) | 提供离线私有化部署的大语言模型服务，用于基准测试与核心推理 |
| **大模型层 (云端服务)** | OpenAI API, `openai` 官方库 | 通过标准协议对接强大的云端闭源模型（如 GPT-4o，GLM），提供泛用的高阶推理能力 |
| **数据抽取与解析** | pdfplumber, PyMuPDF | 针对高规制 SEC 财报 PDF 进行高精度结构推断与双轨（表格/文本）分离内容提取 |
| **向量存储与聚合计算** | LanceDB, PyArrow | 提供自带元数据过滤机制的高性能轻量级向量检索，以及列存级别的高效内存结构 |
| **外部通信与可视化** | httpx, Plotly | 与 SEC EDGAR 等外部 HTTP 接口进行异步/代理通信，并在前端进行财务数据的交互式图表渲染 |
| **测试与质量工程** | Pytest, Ruff | 测试驱动框架驱动 E2E 全链路集成与单元用例执行，静态代码检查维护 PEP 8 及强架构规范一致性 |
| **包管理与环境配置** | uv, python-dotenv, pydantic-settings | 毫秒级高速包及环境构建管理、运行时环境变量与配置文件解析控制 |B