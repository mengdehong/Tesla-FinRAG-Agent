# Tesla FinRAG Agents Implementation Plan

更新时间：2026-03-07-23:30

## 1. 目标

本文件是 Tesla FinRAG 项目的总实施路线图，用于把项目拆分为多个 OpenSpec change，并将每个阶段进一步拆成可并行的 AI agent 任务包。

该文档服务于三类目标：

1. 统一项目开发顺序，避免在没有基础契约时并发开发。
2. 固定每个阶段的完成定义，使 agent 之间可以独立执行、可回收、可验证。
3. 为后续其他 AI agent 提供稳定的任务卡模板与协作边界。

## 2. 执行方式

项目采用 OpenSpec 的多阶段多 change 流程，而不是将全部工作放入一个总 change。

每个阶段都执行同样的流程：

1. `openspec new change <change-name>`
2. 创建 `proposal.md`
3. 创建 `design.md`
4. 创建 `specs/<capability>/spec.md`
5. 创建 `tasks.md`
6. 补充 `agent-pack.md`
7. 通过 `openspec validate <change-name>`
8. 再交付给实现型 agent 执行

## 3. 阶段与顺序

### Phase 0: Program Planning Baseline

- 输出本文件 `docs/research/02-AgentsImplPlan.md`
- 固定 OpenSpec 路线图与任务卡模板
- 不实现业务代码，只冻结项目实施方式

### Phase 1: `bootstrap-project-foundation`

目标：完成 Python 项目骨架、质量门禁、类型模型、配置边界和核心 service/repository contracts。

完成后应具备：

- `uv` 驱动的可复现实验环境
- `src/` 与 `tests/` 结构
- `ruff` / `pytest` 基础门禁
- 可被后续阶段复用的 typed models 与 settings
- 清晰的 ingestion / retrieval / answering 边界

### Phase 2: `add-dual-track-ingestion-pipeline`

目标：建立 Tesla filing 数据的双轨摄取层。

完成后应具备：

- 可枚举目标 filing 的 manifest
- 对缺失文档显式报缺而非静默失败
- HTML/PDF 叙述文本与表格标准化
- XBRL/companyfacts 结构化 facts 标准化
- `period_key`、来源元数据和落盘结构统一

### Phase 3: `add-hybrid-retrieval-and-answer-pipeline`

目标：建立从 query 到 grounded answer 的核心 RAG 与计算链路。

完成后应具备：

- 年份、季度、指标和问题类型解析
- BM25 + vector 的 hybrid retrieval
- metadata filter 与证据聚合
- 财务计算服务
- 带引用、带计算步骤、带调试信息的答案载荷

### Phase 4: `add-streamlit-evaluation-workbench`

目标：完成 demo 交互层、复杂问题评测集、失败案例分析和回归流程。

完成后应具备：

- Streamlit 查询界面
- 查询范围过滤
- 引用片段与调试信息展示
- 至少 5 个复杂问题
- 至少 5 个失败或低质案例分析
- 可重复运行的回归 harness

## 4. 核心接口冻结

以下类型和接口从 Phase 1 开始冻结，后续 change 只能扩展字段或增加实现，不应改语义：

- `FilingDocument`
- `SectionChunk`
- `TableChunk`
- `FactRecord`
- `QueryPlan`
- `EvidenceBundle`
- `AnswerPayload`

以下服务边界固定：

- `FilingSource`
- `CorpusBuilder`
- `HybridRetriever`
- `QueryPlanner`
- `FinancialCalculator`
- `AnswerService`

以下存储策略固定：

- 通过 repository 模式隔离底层后端
- 默认向量与混合检索后端为 LanceDB
- 数值事实以 XBRL/companyfacts 为权威来源

## 5. Agent 协作规则

每个 change 至少配置 1 个 integrator agent 和 2 到 4 个 worker agents。

### Integrator 责任

- 维护 OpenSpec artifacts 的一致性
- 冻结共享接口与字段命名
- 生成并派发任务卡
- 合并 worker 输出
- 运行 change 级验证命令

### Worker 责任

- 仅在任务卡定义的 `write_scope` 内改动文件
- 不修改其他 agent 的契约与共享边界
- 完成任务后附带验证命令结果
- 若发现设计冲突，先回报 integrator，再决定是否更新 artifacts

## 6. 任务卡模板

每张交付给其他 AI agent 的任务卡必须包含以下字段：

- `task_id`
- `change_name`
- `goal`
- `depends_on`
- `write_scope`
- `do_not_touch`
- `inputs`
- `deliverables`
- `validation_commands`
- `done_when`

推荐格式：

```md
## <task_id>

- Change: <change_name>
- Goal: <goal>
- Depends on: <depends_on>
- Write scope: <write_scope>
- Do not touch: <do_not_touch>
- Inputs: <inputs>
- Deliverables: <deliverables>
- Validation commands: <validation_commands>
- Done when: <done_when>
```

## 7. 并行拆分原则

- 优先按目录边界拆分，而不是按函数混写
- 优先让一个 agent 只拥有一个子系统
- 共享模型与接口只能由 integrator 调整
- 验证与测试任务可以独立分配，但不得绕过上游契约

推荐写域划分：

- `src/ingestion`
- `src/retrieval`
- `src/answering`
- `src/ui`
- `tests/ingestion`
- `tests/retrieval`
- `tests/answering`
- `tests/ui`

## 8. 阶段门禁

### Phase 1 门禁

- `uv sync`
- `uv run pytest -q`
- `uv run ruff check .`

### Phase 2 门禁

- manifest 能列出目标 filings
- 缺失文档被标记为 gap
- narrative、table、facts 均能标准化
- 至少存在一组 ingestion regression tests

### Phase 3 门禁

- 带年份和季度的查询能命中过滤后的证据
- 数值计算不依赖大模型自由推算
- 答案返回引用和计算步骤
- 至少存在文本、数值、文本+表格三类集成测试

### Phase 4 门禁

- Streamlit demo 可运行
- 调试信息面板可展示
- 评测集与失败案例分析可重复运行
- 回归 harness 能输出结果摘要

## 9. 当前已知约束

- 当前仓库只有原始数据、研究文档和一个简单的 XBRL 下载脚本，尚未初始化 Python 项目骨架。
- 当前 `data/raw/` 中已存在 2021 Q1 到 2025 Q3 的 10-Q，以及 2020-2024 的 FY 10-K PDF。
- 目标语料若覆盖完整 2025 财年，则 ingestion 阶段需要显式处理 2025 FY 10-K 缺口。
- 原始 `~/docs/research` 目录不存在，因此本规划文档落在仓库内的 `docs/research/`。
