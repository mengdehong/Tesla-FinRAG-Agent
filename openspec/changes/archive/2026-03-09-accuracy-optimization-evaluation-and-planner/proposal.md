## Why

当前 Tesla FinRAG 系统的评测通过率为 33-44%，但这个分数同时受到**真实系统错误**和**评测规则误伤**两类因素的污染。评测使用纯关键词包含判分（`expected_answer_contains`），导致事实正确但措辞不同的答案被判失败（如 BQ-001 已输出正确的 18.80% 但因缺少原始收入数字而失败，BQ-008 已输出正确数值但因缺少 `result` 一词而失败）。同时，Query Planner 对 margin 类问题会错误映射到不存在的伪概念（如 `custom:OperatingMarginPercent`），Calculator 依赖概念列表顺序而非显式意图来决定分子分母方向。这些问题的共同根源是系统设计与工程实现的结构性缺陷，而非模型能力不足。

本轮优化需要先让评测可信（区分误伤与真实失败），再修复 Query Planner 的计算意图表达，使后续优化能在可靠指标下推进。

## What Changes

- 引入**结构化断言判分**（structured judge）作为评测主口径，保留旧关键词判分为 legacy 对比口径
- 扩展 `BenchmarkQuestion` 模型，新增 `expected_status`、`expected_facts`、`expected_calc`、`expected_period_semantics` 字段
- 扩展 `QuestionResult` 模型，新增 `legacy_passed`、`structured_passed`、`judge_breakdown` 字段
- 为每题保存完整调试数据（query plan、retrieved facts、calculation trace、judge breakdown）
- 扩展 `QueryPlan` 模型，新增 `calculation_intent`、`calculation_operands`、`requires_step_trace`、`answer_shape` 字段
- 重构 `RuleBasedQueryPlanner`：移除伪概念映射（`custom:OperatingMarginPercent`、`custom:GrossMarginPercent`），改为生成显式 ratio intent + operands
- 修改 `GroundedAnswerComposer` 使其消费新的 `calculation_intent` 字段来路由计算逻辑
- 迁移 9 道基准题到双轨评测

## Capabilities

### New Capabilities

- `structured-evaluation`: 结构化断言评测体系，支持数值容差验证、事实命中检查、状态断言，替代纯关键词包含判分作为主评测口径

### Modified Capabilities

- `grounded-financial-qa`: QueryPlan 新增计算意图字段，Planner 移除伪概念映射改为显式 ratio/step-trace intent，Composer 使用 calculation_intent 路由计算
- `demo-evaluation-workbench`: 评测 Runner 支持双轨判分（legacy + structured），QuestionResult 扩展诊断字段

## Impact

- 受影响代码：`src/tesla_finrag/models.py`（QueryPlan 扩展）、`src/tesla_finrag/evaluation/models.py`（BenchmarkQuestion/QuestionResult 扩展）、`src/tesla_finrag/evaluation/runner.py`（双轨判分）、`src/tesla_finrag/planning/query_planner.py`（伪概念移除 + intent 生成）、`src/tesla_finrag/answer/composer.py`（intent 路由）
- 受影响数据：`data/evaluation/benchmark_questions.json`（新增结构化断言字段）
- 受影响 API：`QueryPlan` 模型添加可选字段（向后兼容）、`BenchmarkQuestion` 和 `QuestionResult` 添加可选字段（向后兼容）
- 测试：需新增 Planner intent 生成单元测试、structured judge 单元测试、margin 查询集成测试
