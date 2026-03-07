# Tesla FinRAG 项目初始化研究报告

更新时间：2026-03-07-22:00

## 1. 结论摘要

这个项目不应被实现为一个“只对 PDF 做向量检索”的轻量 Demo，而应被设计成一个兼顾面试交付与后续扩展的金融问答系统原型。对于 Tesla 2021-2025 财报这种跨年份、多文档、涉及数字计算与文字关联的问题，单纯依赖 PDF 文本切块会在三类场景中明显失效：

1. 财务表格容易被切坏，导致数字列和列头脱离。
2. 同一个季度的叙述性解释与财务数字分散在不同章节，检索阶段难以重新关联。
3. 跨年度趋势、季度环比、累计计算等问题需要显式的时间对齐与数值计算，而不是仅靠生成模型“猜”。

因此，推荐的主路线是：

- 数据源采用 `SEC filings + PDF/HTML + XBRL/companyfacts` 双轨方案。
- 检索采用 `关键词/全文 + 向量` 的混合检索，而不是纯向量检索。
- 问答链路采用 `查询理解 -> 证据召回 -> 时间/指标对齐 -> 计算 -> 带引用生成`，而不是单轮 prompt。
- 项目技术栈采用 `Python 3.12 + uv + Pydantic + PyMuPDF/pdfplumber + LanceDB + Streamlit + pytest`。
- 模型策略采用“双模式并重”：默认支持 API 模式以保证效果，保留本地模式接口以控制成本和依赖。


## 2. 项目背景与目标

根据 [docs/PROJECT.md](/home/wenmou/Projects/Tesla-FinRAG-Agent/docs/PROJECT.md)，本项目的核心任务是构建一个能够处理 Tesla 2021、2022、2023、2024、2025 年全部 10-K 与 10-Q 财报的智能问答系统。问题类型不只是“找到一句相关话”，而是需要同时支持：

- 跨年度趋势比较
- 不同文档间的内容对比
- 文本与表格联合推理
- 显式数字计算
- 失败案例追踪与分析

根据 [docs/MyPrefer.md](/home/wenmou/Projects/Tesla-FinRAG-Agent/docs/MyPrefer.md)，当前偏好已给出几个明确方向：

- 包管理：`uv`
- UI：`Streamlit`
- PDF 解析：`pdfplumber + pymupdf`，可选云端解析
- 向量数据库：优先可替换的 Repository 模式
- Embedding：支持 DashScope、HuggingFace、本地轻量模型
- 检索架构：`BM25 + 余弦相似度` 双路增强


## 3. 当前仓库现状

截至本次调研，仓库当前只包含：

- 任务说明 `docs/PROJECT.md`
- 技术偏好说明 `docs/MyPrefer.md`
- OpenSpec 配置与技能目录


## 4. 数据范围与语料规划

### 4.1 推荐语料范围

以 2026-03-07 为基准，SEC 官方 submissions 数据已经覆盖 Tesla 从 2021 Q1 到 2025 FY 的目标财报，其中 2025 年 10-K 已于 2026-01-29 提交。对于本项目，建议默认语料范围如下：

- 2021 FY 10-K
- 2021 Q1/Q2/Q3 10-Q
- 2022 FY 10-K
- 2022 Q1/Q2/Q3 10-Q
- 2023 FY 10-K
- 2023 Q1/Q2/Q3 10-Q
- 2024 FY 10-K
- 2024 Q1/Q2/Q3 10-Q
- 2025 FY 10-K
- 2025 Q1/Q2/Q3 10-Q

总计按 20 份 filings 规划。

### 4.2 为什么不应只依赖 PDF

招聘任务以“PDF 财报解析”为入口，但从工程实现角度看，只依赖 PDF 会让系统在数字问答上承受不必要的损失。推荐采用以下权责划分：

- `PDF/HTML`：负责叙述文本、原始段落引用、表格原貌、页码/章节定位
- `XBRL/companyfacts`：负责结构化财务事实、时间维度对齐、数值计算的权威来源

这样做的原因：

1. PDF 更适合做“引用展示”和原文证据追溯。
2. XBRL 更适合做“某季度某指标是多少”这类结构化查询。
3. 复杂问题通常同时需要两者。例如，先定位利润率最低的季度，再回到该季度 MD&A 中找解释。

### 4.3 推荐数据获取策略

推荐构建一个 `SECFilingSource` 数据源适配器，统一完成：

- 通过 SEC submissions API 枚举目标 filing
- 下载 filing HTML 和必要的附件
- 若存在稳定 PDF 入口则保留 PDF 副本
- 下载 companyfacts/XBRL JSON
- 为每份文档生成统一的本地元数据记录

本地建议形成如下数据落盘结构：

```text
data/
  raw/
    filings/
    companyfacts/
  normalized/
    filings/
    sections/
    tables/
    facts/
  derived/
    evaluation/
    debug/
  index/
    lancedb/
```

## 5. 架构设计

### 5.1 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│                         Data Sources                         │
│    SEC submissions / filing HTML / PDF / XBRL companyfacts  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                 Ingestion & Normalization Layer             │
│  下载、解析、章节识别、表格提取、period_key 对齐、元数据标准化 │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                       Corpus Layer                           │
│ SectionChunk / TableChunk / FactRecord / FilingDocument      │
└──────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┴────────────────────┐
         ▼                                         ▼
┌───────────────────────┐               ┌────────────────────────┐
│  Lexical / FTS Index  │               │   Dense Vector Index   │
│  year, quarter, terms │               │  semantic embeddings   │
└───────────────────────┘               └────────────────────────┘
         └────────────────────┬────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  Query Planning & Retrieval                  │
│ intent classification / scope filters / hybrid fusion        │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                 Evidence Linking & Calculator                │
│  同期文本、表格、数值事实聚合；计算同比、环比、求和、排序等     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Answer Generation Layer                   │
│        带引用回答、计算步骤、检索调试信息、失败原因           │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Streamlit Application                     │
│  查询范围选择 / 问题输入 / 答案 / 引用 / 调试面板 / 评测入口  │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 关键设计原则

#### 原则一：以 canonical schema 连接各个阶段

不要让解析器、检索器、UI 都直接操作“原始字符串”。项目必须先统一内部数据模型，再允许不同模块围绕该模型工作。推荐的核心类型如下：

| 类型 | 作用 | 关键字段 |
| --- | --- | --- |
| `FilingDocument` | 描述一份 filing 的整体信息 | `company`, `form_type`, `filing_date`, `fiscal_year`, `period_key`, `accession_no` |
| `SectionChunk` | 叙述性文本块 | `section_path`, `heading`, `content`, `page_span`, `period_key`, `keywords` |
| `TableChunk` | 表格块 | `table_title`, `markdown_table`, `json_rows`, `page_span`, `period_key`, `metrics` |
| `FactRecord` | 结构化财务事实 | `metric_name`, `value`, `unit`, `period_key`, `source_form`, `source_accn` |
| `QueryPlan` | 查询理解结果 | `query_type`, `target_periods`, `metrics`, `needs_calculation`, `filters` |
| `AnswerPayload` | 返回给 UI 的标准结果 | `answer`, `citations`, `calc_steps`, `retrieval_debug`, `confidence` |

#### 原则二：分块应遵循财报结构，而不是固定 token 切分

推荐使用“章节边界优先”的分块逻辑：

- 一级边界：文档级别（10-K / 10-Q / 年份 / 季度）
- 二级边界：章节级别（MD&A、Risk Factors、Financial Statements、Notes）
- 三级边界：子标题级别（Liquidity、Automotive Gross Margin、Cash Flows 等）
- 表格：永远独立成块，不和正文合并

每个 chunk 都应至少携带：

- `form_type`
- `fiscal_year`
- `fiscal_quarter`
- `period_key`
- `section_path`
- `page_span`
- `source_filename`
- `accession_no`

这组元数据是后续混合检索、范围过滤和证据聚合的基础。

#### 原则三：复杂问题必须允许“先查文本，再查表，再计算”

例如问题：

> 2022 年哪份季报提到了供应链挑战，并且该季度的营收环比变化如何？

这不是一步完成的问题，而是至少包含三步：

1. 先在叙述文本中找出“供应链挑战”出现在哪个季度。
2. 再定位该季度的营收数据。
3. 最后计算与上一季度的环比变化。

因此问答系统必须内置一个最小的“查询规划器”，而不是把用户问题直接丢给最终生成模型。

## 6. 技术栈建议
 列表 | 便于标准化表格和导出对比结果 |
### 6.1 推荐主栈

| 领域 | 推荐方案 | 备选方案 | 建议理由 |
| --- | --- | --- | --- |
| Python 运行时 | Python 3.12 | Python 3.11 | 生态稳定，语法现代，和主流 AI/PDF 库兼容好 |
| 包管理 | `uv` | `pip + venv` | 与现有偏好一致，依赖解析快，锁文件清晰 |
| 配置管理 | `pydantic-settings` | `python-dotenv` | 配置结构化，便于区分 API 与本地模式 |
| HTTP/下载 | `httpx` | `requests` | 异步/同步统一，后续做并发下载更灵活 |
| PDF/版面解析 | `pymupdf` + `pymupdf4llm` | `pdfminer.six` | 提取速度快，支持页面结构与表格候选 |
| 表格提取回退 | `pdfplumber` | `camelot` | 对复杂财报表格更容易调参和调试 |
| HTML 解析 | `beautifulsoup4` + `lxml` | 纯正则 | 对 SEC filing HTML 更可靠 |
| 结构化表格/数据处理 | `pandas` | 纯 Python
| 向量/检索数据库 | `lancedb` | `chromadb`, `faiss` | 更适合本地 hybrid search 与文件化部署 |
| Embedding client | `openai` SDK 统一封装 | `sentence-transformers` 直连 | 可通过 `base_url` 兼容多家 API |
| 本地 embedding | 轻量英文 embedding 模型 + ONNX 可选 | 全量大模型 | 成本低、部署轻、适合本地模式 |
| UI | `streamlit` | `gradio` | 与偏好一致，数据面板和调试信息更好组织 |
| 测试 | `pytest` | `unittest` | 与数据处理/回归测试生态更契合 |
| 代码质量 | `ruff` | `flake8 + black` | 性能快，配置简单 |

### 6.2 为什么推荐 LanceDB 作为主选

当前候选里，`ChromaDB`、`FAISS` 和 `LanceDB` 都能完成基础向量检索，但本项目真正需要的是：

- 本地可落盘
- metadata filter
- full-text / keyword 检索
- 向量检索
- hybrid fusion
- 后续支持 rerank

对比而言：

- `FAISS` 只适合作为纯向量库，不擅长承担完整检索层。
- `ChromaDB` 做向量检索很顺手，但如果要实现稳定的 BM25/FTS 混合检索，通常还要额外拼接其他组件。
- `LanceDB` 更适合作为首版“一库完成”的本地方案。

因此推荐：

- 默认主实现：`LanceDB`
- 备选最简实现：`ChromaDB + 额外 BM25 组件`
- 实验性基线：`FAISS`

### 6.3 模型与向量策略

#### API 模式

API 模式的目标是优先保证质量和交付速度。推荐：

- 统一使用 OpenAI-compatible 接口抽象
- Embedding 优先支持：
  - OpenAI `text-embedding-3-small`
  - DashScope 文本向量接口
- Generation 优先支持：
  - OpenAI 兼容的通用聊天模型
  - DashScope 或其他兼容提供方

API 模式优点：

- 效果稳定
- 开发快
- 便于首版调试
- 与招聘任务的演示目标更一致

#### 本地模式

本地模式的目标是保留低成本和可替换能力。推荐：

- 本地 embedding 采用轻量英文模型
- 推理服务采用 OpenAI-compatible 本地端点
- 通过配置切换 provider，而不是改业务代码

本地模式不要求首版和 API 模式达到完全一致的回答质量，但必须做到：

- 接口兼容
- 配置可切换
- 索引维度与 embedding 模型绑定清晰

### 6.4 为什么不推荐首版引入 LangChain/LlamaIndex

这类框架在原型阶段看起来快，但对本项目存在三个实际问题：

1. 复杂问答的真正难点在“财报结构化、时间对齐、数值计算”，不是把若干通用组件串起来。
2. 招聘任务要求失败案例分析，链路越黑盒，越难解释失败原因。
3. 当前项目规模不大，用原生 Python service 层 + Pydantic 更容易保持可控。

因此建议首版以 typed service architecture 为主：

- `ingest_service`
- `normalize_service`
- `retrieval_service`
- `planning_service`
- `calculation_service`
- `answer_service`
- `evaluation_service`

## 7. 解析、索引与检索设计

### 7.1 解析策略

推荐解析链路如下：

1. 下载 filing HTML、元数据和 companyfacts。
2. 对 PDF 或 HTML 做章节识别。
3. 对叙述性内容提取为 `SectionChunk`。
4. 对表格提取为 `TableChunk`，同时保留 Markdown 形式和结构化 JSON 行列。
5. 对 XBRL/companyfacts 生成 `FactRecord`。
6. 对三类对象统一补齐 `period_key`、`form_type`、`fiscal_year`、`section_path` 等元数据。

建议设置最小质量检查：

- 若表格列头丢失，则标记为低置信度并触发回退解析。
- 若 chunk 没有识别到 `period_key`，则不得入主索引。
- 若引用信息无法回溯页码或来源文件，则不得进入最终 answer citation。

### 7.2 分块策略

推荐参数而不是一刀切：

- 叙述文本：按章节切分，再在章节内做适度长度控制
- 表格：一表一块
- 表格说明文字：可作为单独 chunk，与表格通过 `table_id` 关联
- 超长章节：允许子标题再切分

不要做的事情：

- 不要把整页当成一个 chunk
- 不要把表格与正文硬拼在同一个 chunk
- 不要只按 token 数固定切分所有内容

### 7.3 混合检索策略

推荐三路检索并在证据层融合：

1. `lexical/FTS lane`
   - 面向年份、季度、财务术语、工厂名称等强关键词
2. `dense semantic lane`
   - 面向同义表达、长问题、叙述性解释
3. `structured facts lane`
   - 面向营收、研发费用、现金流、毛利率等标准财务指标

融合时优先按这些条件聚合：

- `period_key`
- `form_type`
- `fiscal_year`
- `metric_name`

这样才能把“同季度的解释段落”和“同季度的数字事实”重新连回去。

### 7.4 查询规划与计算器

推荐先做一个轻量版查询分类器，把用户问题分成至少四类：

- `narrative_compare`
- `table_lookup`
- `numeric_calculation`
- `hybrid_reasoning`

然后根据类型选择执行路径：

- 叙述比较：优先文本检索
- 表格定位：优先表格与关键词检索
- 数字计算：优先 facts/table，必要时显式调用计算器
- 混合问题：先确定季度或指标，再回溯相关文本解释

计算器建议支持：

- 同比/环比
- 多季度求和
- 排名/最大值/最小值
- 两组指标差值与比例

## 8. 返回结果与 UI 设计

### 8.1 返回结果结构

UI 与后端之间建议统一 `AnswerPayload`：

```json
{
  "answer": "...",
  "citations": [],
  "calc_steps": [],
  "retrieval_debug": {},
  "confidence": 0.0
}
```

其中：

- `answer`：最终自然语言答案
- `citations`：引用的 chunk/table/fact 列表
- `calc_steps`：涉及计算时的步骤说明
- `retrieval_debug`：可选的召回明细和过滤条件
- `confidence`：可选，用于 UI 呈现保守提示

### 8.2 Streamlit 页面建议

首版 UI 不应追求复杂，而应强调调试可见性。建议包含：

- 查询范围选择
  - 年份范围
  - 仅 10-K / 仅 10-Q / 全部
- 问题输入框
- 回答区
- 引用区
- 检索调试区
  - 命中的 chunks
  - 命中的 tables
  - 命中的 facts
  - 查询规划结果
- 评测页签
  - 运行 golden set
  - 查看失败样例

## 9. 建议目录与模块边界

建议尽早按如下结构组织项目，而不是把逻辑堆在单文件脚本里：

```text
src/
  tesla_finrag/
    config/
    domain/
    ingest/
    parse/
    normalize/
    retrieval/
    qa/
    eval/
    ui/

tests/
  fixtures/
  golden/
  integration/

data/
  raw/
  normalized/
  derived/
  index/
```

建议对外暴露以下命令入口：

- `sync-filings`：同步目标 filings 与元数据
- `build-corpus`：解析并标准化 chunk/table/fact
- `build-index`：构建向量与全文索引
- `ask`：执行单次问答
- `run-eval`：运行测试集与失败分析

## 10. 开发路线图

### Phase 0：项目骨架

目标：

- 用 `uv` 初始化项目
- 建立 `src/`、`tests/`、`data/` 目录
- 配置 `.env` / settings
- 引入基本依赖与代码质量工具

退出标准：

- 项目能完成环境安装与最小命令运行

### Phase 1：数据下载与标准化

目标：

- 打通 SEC filings 下载
- 能产出 `FilingDocument`
- 能保存 HTML/PDF/companyfacts 原始文件
- 能形成本地 manifest

退出标准：

- 至少 4 份 filing 成功落盘并可回溯

### Phase 2：解析与分块

目标：

- 产出 `SectionChunk`
- 产出 `TableChunk`
- 产出 `FactRecord`
- 建立 `period_key` 对齐逻辑

退出标准：

- 2 份 10-K + 2 份 10-Q 解析结果可用于检索

### Phase 3：混合检索与问答

目标：

- 建立 LanceDB 索引
- 实现 lexical + dense + facts 检索
- 实现 QueryPlan
- 实现 Calculator
- 能返回带引用答案

退出标准：

- 至少 5 个复杂问题可跑通主链路

### Phase 4：评测与失败分析

目标：

- 构建 golden set
- 输出检索命中情况
- 沉淀至少 5 个失败案例
- 给出改进建议

退出标准：

- 满足招聘任务中关于高阶测评与失败评估的要求

### Phase 5：UI 与演示包装

目标：

- 完成 Streamlit 界面
- 支持过滤条件和调试展示
- 支持评测结果查看

退出标准：

- 形成可演示版本

## 11. 测试与评测建议

### 11.1 必做测试

1. 解析回归测试
   - 确认章节提取、表格提取、页码保留、元数据完整
2. 索引测试
   - 确认年份、季度、术语过滤和 hybrid 检索都能生效
3. 计算测试
   - 确认求和、同比、环比、最大值/最小值计算正确
4. 问答集成测试
   - 确认答案包含正确引用和计算步骤

### 11.2 Golden Set 设计建议

至少覆盖以下问题类型：

- 跨年度叙述变化
- 多季度数字聚合
- 文本与表格联动
- 时间顺序推理
- 财务指标排序或极值定位

### 11.3 失败分析模板

每个失败案例建议固定记录：

- 问题原文
- 理想答案
- 系统回答
- 失败表象
- 检索阶段问题
- 解析阶段问题
- 推理/计算阶段问题
- 根本原因
- 可落地改进方案

## 12. 风险与权衡

### 12.1 PDF 解析质量风险

财报中的复杂表格、跨页表格、脚注结构都可能导致解析错误。应对策略：

- 主解析器和回退解析器并存
- 低置信度表格打标
- 关键指标优先从 XBRL 校验

### 12.2 PyMuPDF 许可证风险

PyMuPDF 在商业场景下需要认真评估许可证条件。对于当前项目原型，它是一个高效率选项，但文档中必须明确这个风险。如果后续需要更宽松的开源路径，可以退回：

- `pdfplumber + HTML parser`

### 12.3 模型供应商差异风险

不同 API 或本地模型在：

- embedding 维度
- 检索效果
- 生成稳定性
- token 成本

上都会有差异，因此必须把 provider 抽象为配置，而不是写死在业务逻辑里。

### 12.4 过度框架化风险

如果首版过早引入过多 RAG 框架、Agent 框架或评测框架，会拖慢迭代速度并降低可解释性。首版建议优先保证：

- 数据结构清晰
- 检索链路可调试
- 失败原因可复盘

## 13. 最终建议

这份项目的最佳起步方式，不是先做 UI，也不是先做 prompt，而是按下面顺序推进：

1. 先把 Tesla 2021-2025 的 filing 数据源和 canonical schema 定下来。
2. 再把章节、表格、XBRL facts 三类证据打通。
3. 然后实现支持时间过滤和财务术语命中的混合检索。
4. 最后再叠加计算器、回答生成、评测与 UI。

如果严格按这个顺序走，项目的每个阶段都有清晰产物，后续无论是为了完成招聘任务，还是为了扩展成更完整的 FinRAG 系统，都不会推倒重来。

## 14. 参考资料

- uv Projects: <https://docs.astral.sh/uv/concepts/projects/layout/>
- Streamlit conversational apps: <https://docs.streamlit.io/develop/tutorials/chat-and-llm-apps/build-conversational-apps>
- Streamlit cache_data: <https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data>
- LanceDB hybrid search: <https://lancedb.com/docs/search/hybrid-search/>
- PyMuPDF `Page.find_tables`: <https://pymupdf.readthedocs.io/en/latest/page.html#Page.find_tables>
- PyMuPDF4LLM API: <https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html>
- pdfplumber table extraction: <https://github.com/jsvine/pdfplumber#extracting-tables>
- SEC EDGAR APIs: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- OpenAI Embeddings guide: <https://platform.openai.com/docs/guides/embeddings>
- Alibaba Cloud Model Studio embeddings: <https://www.alibabacloud.com/help/en/model-studio/text-embedding-syn-text-embedding>
