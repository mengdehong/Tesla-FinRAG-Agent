# Tesla FinRAG Agent

一个面向 Tesla 10-K / 10-Q 财报的金融问答 RAG 项目，关注跨年份检索、财务计算，以及表格与叙述信息的联合引用。

### 阅读入口

1. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)    看系统分层、核心模块、数据流设计。
2. [docs/DELIVERY.md](docs/DELIVERY.md)   看当前交付结果、评测表现、阶段性结论。
3. [docs/DECISION.md](docs/DECISION.md)   看关键技术选型与设计取舍。

### 这个项目解决什么问题

- 面向上市公司财报问答，支持 Tesla 10-K / 10-Q 多年份语料。
- 处理跨文档问题，而不局限于单篇文档中的局部事实。
- 处理财务计算类问题，而不只生成自然语言回答。
- 结合表格数据与管理层叙述，保留答案来源。

### 与招聘任务对应关系

| 招聘任务 | 本项目实现 |
|---|---|
| 环境管理 | 使用 `uv` 管理依赖，提供 `pyproject.toml` 与 `uv.lock` |
| 数据处理与索引 | 解析财报 PDF，抽取文本、表格与页码等来源信息，并构建索引 |
| 分块策略 | 基于文档结构进行语义分块，表格作为独立完整单元保留 |
| 向量化 | 对文本块与表格块进行向量化并写入向量数据库 |
| 混合检索 | 实现 BM25 + 向量检索，增强财务术语、年份、季度命中能力 |
| 复杂问答 | 支持跨文档检索、数值计算、文本证据关联与多步回答 |
| 高阶测评 | 设计复杂测试集并进行失败案例分析 |
| 交互界面 | 使用 Streamlit 提供检索问答界面与调试信息展示 |

### 在线演示
演示地址：https://finrag.wenmou.site/
<img src="docs/images/demo1.png" alt="image-20260310140658349|" style="zoom:25%;" />

**示例问题：**
- What was Tesla's total revenue in FY2023?
- Tesla 2022 Q3 的总营收是多少？
- How much did Tesla's total revenue grow from FY2022 to FY2023?
- Which was higher, Tesla's total revenue in FY2023 or FY2024?
- Tesla 2022 Q3 面临了哪些供应链挑战？

**局限性：**
- 由于服务器资源限制，仅提供remote (OpenAI-compatible) 模式的在线演示
- 由于当前输入处理策略的尚未处理完全，优先使用英文提问，以获得更好的结果。且因为Planner模块的设计，延迟较高，正在优化中。   

### 代码入口

- [src/tesla_finrag/](src/tesla_finrag/)：核心源码
- [tests/](tests/)：单元测试与集成测试
- [data/raw/](data/raw/)：原始财报数据

### 本地验证
请在.env文件中设置好环境变量（如OPENAI_API_KEY），然后运行以下命令：

```bash
git clone https://github.com/mengdehong/Tesla-FinRAG-Agent.git --depth=1
cd Tesla-FinRAG-Agent
uv sync
uv run python -m tesla_finrag ingest 
uv run streamlit run app.py
```
