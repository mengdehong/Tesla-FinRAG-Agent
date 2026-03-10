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

### 项目内容

- 设计财报解析与索引链路，处理文本块、表格块和来源元数据。
- 实现混合检索能力，兼顾财务术语精确命中与语义召回。
- 构建财务问答流程，覆盖检索、计算、证据组织和答案生成。
- 建立评测与失败分析流程，用复杂问题集检查系统瓶颈。

### 在线演示
演示地址：

### 代码入口

- [src/tesla_finrag/](src/tesla_finrag/)：核心源码
- [tests/](tests/)：单元测试与集成测试
- [data/raw/](data/raw/)：原始财报数据

### 快速验证

```bash
git clone https://github.com/mengdehong/Tesla-FinRAG-Agent.git --depth=1
cd Tesla-FinRAG-Agent
uv sync
uv run streamlit run app.py
```
