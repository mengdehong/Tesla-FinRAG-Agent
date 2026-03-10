# Tesla FinRAG 准确率优化实施计划

更新时间：2026-03-09-12:10

## 0. 2026-03-09 实施复盘（本次分支实际落地）

### 0.1 分支与归档信息

- 分支：`accuracy-optimization-evaluation-and-planner`
- 分支提交：`8ac912b`（`feat(evaluation): 完成准确率评测优化并归档openspec变更`）
- 合并提交（dev）：`9855b72`（`merge(dev): 合并准确率优化分支`）
- OpenSpec 归档：
- `openspec/changes/archive/2026-03-09-accuracy-optimization-evaluation-and-planner/`
- `openspec/changes/archive/2026-03-09-improve-bq003-fact-resolution-and-table-fallback/`
- 说明：主 change 在执行 `openspec archive` 时自动 spec sync 遇到 header 对齐问题，后续以 `--skip-specs` 完成归档；任务状态为 complete。

### 0.2 实际修改文件（按模块归类）

- 评测与判分：
- `src/tesla_finrag/evaluation/runner.py`
- `src/tesla_finrag/evaluation/models.py`
- `data/evaluation/benchmark_questions.json`
- 规划与答案链路：
- `src/tesla_finrag/models.py`
- `src/tesla_finrag/planning/query_planner.py`
- `src/tesla_finrag/answer/composer.py`
- `src/tesla_finrag/evidence/linker.py`
- `src/tesla_finrag/evaluation/workbench.py`
- `src/tesla_finrag/provider.py`
- 测试新增/增强：
- `tests/test_evaluation.py`
- `tests/test_period_aware.py`
- `tests/test_planner_intent.py`
- `tests/test_composer_intent_routing.py`
- `tests/test_phase_c.py`
- OpenSpec 归档产物：
- `openspec/changes/archive/2026-03-09-accuracy-optimization-evaluation-and-planner/**`
- `openspec/changes/archive/2026-03-09-improve-bq003-fact-resolution-and-table-fallback/**`

### 0.3 本次落地思路（从“刷分”转向“可信判分 + 可诊断”）

- 评测从单一关键词命中升级为双轨：保留 legacy judge，同时以 structured judge 作为主口径。
- structured judge 增加两道关键门禁：
- `period facts gate`：不仅看概念是否命中，还校验每个 required period 的事实覆盖。
- `operation gate`：当题目配置 `expected_calc` 时，要求 `expected_calc.operation` 与链路中的 `calculation_intent` 一致（`lookup` 兼容 `step_trace`）。
- Answer 链路补充 `retrieval_debug.calculation_intent`，使评测可以校验“算对了什么类型”，而不是只看答案里是否出现某个数字。
- 对 BQ-003 这类复合题，明确将“叙事命中但数值缺证据”的情况判为失败，避免假阳性。

### 0.4 实际效果与验证结果

- 代码质量回归：
- `UV_CACHE_DIR=.uv-cache uv run ruff check .` 通过
- `UV_CACHE_DIR=.uv-cache uv run pytest -q` 通过（`465 passed, 80 skipped`）
- 单例验证（按本次要求，仅跑 BQ-003，不跑全量）：
- 结果：`legacy_passed=True`，`structured_passed=False`，最终 `passed=False`
- 关键诊断：`period_facts_ok=False`，`periods_missing_facts=['2022-12-31','2023-12-31']`
- 结论：新门禁已生效，能够阻止“numeric lane 缺证据但仍被判通过”的误判。

### 0.5 当前结论

- 该分支已完成“准确率优化的评测可信化与核心链路收敛”目标，且已合并回 `dev`。
- 当前评分口径相较旧版更严格，分数可能短期回落，但更接近真实能力，不再被文案命中或概念全局命中掩盖。
- 下一步应在该严格口径下继续补齐 BQ-003 的 numeric 证据链路（facts/table fallback 的 period 级覆盖）。

## 1. 背景与目标

本文件用于沉淀 Tesla FinRAG 当前阶段的准确率优化方案，目标不是单纯追求旧 benchmark 的表面分数，而是优先提升系统的真实问答质量、可解释性和失败可诊断性。

本轮规划遵循以下原则：

1. 优先修复真实链路问题，而不是通过 prompt 或措辞技巧“刷分”。
2. 优先保证数值事实、时间对齐、计算逻辑和证据引用正确。
3. 同步改进评测口径，避免“答案基本正确但仍被误判失败”的情况。

本轮规划周期为 `2-4 周`，目标导向为 `真实质量优先`，并将 `评测体系本身` 纳入优化范围。

## 2. 当前现状与关键发现

### 2.1 当前 baseline 结果

截至 2026-03-08，本地重新运行评测后得到一轮最新结果：

- run id: `3201f868550c`
- 总题数：`9`
- 通过：`4`
- 失败：`5`
- 错误：`0`
- 通过率：`44.44%`

当前仓库中已接受的 `latest baseline` 仍是上一轮：

- run id: `6307bfc151b1`
- 通过率：`33.33%`

因此，当前系统能力相较旧基线已有一定提升，但仍不稳定，也不能将 `44.44%` 直接等价理解为真实世界准确率。

### 2.2 评测口径本身存在偏差

当前 `EvaluationRunner` 的判分方式仍以 `expected_answer_contains` 的关键词包含判断为主。这会带来两个问题：

1. 某些答案在事实层面是正确的，但因为措辞不同而被判失败。
2. 当前指标更像“输出文案命中率”，而不是“事实正确率”。

例如：

- `BQ-001` 已输出正确的同比变化 `18.80%`，但因为未同时包含期望中的两个原始收入数字而失败。
- `BQ-008` 已输出 `96,773,000,000.00`，但因为未出现 `result` 一词而失败。

这说明当前 benchmark 分数被两类因素混合污染：

- 真实系统错误
- 评测规则误伤

### 2.3 当前问题不是“缺一个更强模型”

结合代码、baseline 结果和失败样例，可以确认当前准确率瓶颈主要来自系统设计与工程实现，而不是缺少更大的模型或更长的 prompt。

当前主问题分为三类：

1. `Query Planner` 对部分问题的结构化表达仍然错误。
2. `Calculator / Composer` 在比率、步骤展示、结果模板上存在逻辑缺陷。
3. `Evaluation` 判分逻辑过于脆弱，无法真实反映系统质量。

### 2.4 数据事实层的真实情况需要按“当前数据”重新判断

旧的失败分析文档中有一些结论已经部分过时，不能直接当作当前真相使用。

例如当前 `data/processed/facts/all_facts.jsonl` 中已经存在：

- `us-gaap:ResearchAndDevelopmentExpense`
- `us-gaap:CashAndCashEquivalentsAtCarryingValue`
- `custom:FreeCashFlow`
- `custom:CapitalExpenditure`

但同时也确认：

- `us-gaap:CostOfGoodsAndServicesSold` 当前仍为 `0` 条
- 原始 `companyfacts.json` 中存在 `CostOfRevenue`、`CostOfGoodsSold`
- 这意味着 `BQ-003` 的问题不只是“缺 facts”，更可能是“概念映射与业务语义没有对齐”

因此，后续优化必须以“当前 processed corpus + 当前评测结果”为准，而不是沿用旧报告中的所有结论。

## 3. 优化方向判断：工程为主，算法为辅

本轮准确率优化建议按如下权重推进：

- `70% 工程与系统设计修复`
- `30% 查询理解与计算规则优化`

不建议在首轮把重点放在：

- 更换更大的生成模型
- 引入复杂 agent orchestration
- 依赖 prompt engineering 解决结构性错误

原因如下：

1. 当前主要失败并不是“回答不流畅”，而是 `concept 选错 / ratio 算错 / step trace 缺失 / judge 判错`。
2. 这类问题用更强模型只能部分掩盖，不能形成稳定、可回归的质量提升。
3. 当前仓库已经具备较清晰的 typed pipeline，最适合继续通过工程化方式提高可控性。

## 4. 当前最关键的三个问题

### 4.1 Planner 把问题表达错了

代表问题：`BQ-005`

当前系统会把 `operating margin` 误处理成一个需要直接检索的伪概念 `custom:OperatingMarginPercent`，但该概念并不存在于 fact store 中。

真实应该做的是：

- 对每个 period 分别检索 `OperatingIncomeLoss`
- 同期检索 `Revenues`
- 再按 period 计算 `OperatingIncomeLoss / Revenues`
- 最后做跨期比较与排序

这类错误属于“查询规划层错误”，而不是“检索器没找到东西”。

### 4.2 Calculator / Composer 对结构化问题输出不稳定

代表问题：

- `BQ-002`: gross margin 分子分母方向错误
- `BQ-007`: 已有结果但没有展示请求中的计算步骤
- `BQ-001` / `BQ-008`: 数值正确但回答模板不稳定，不利于评测与用户理解

这说明当前系统虽然已经有显式 calculator，但仍缺：

- 更明确的 calculation intent
- 更稳定的结果模板
- 更明确的“需要步骤展示”信号

### 4.3 Evaluation 无法准确反映真实质量

代表问题：`BQ-001`、`BQ-008`

当前评测会把“事实正确但措辞不一样”的答案直接判为失败，导致：

- 指标不稳定
- 优化方向容易跑偏
- 很难区分“生成表达问题”和“核心事实错误”

因此，如果不先改评测，后续所有准确率优化都会存在观察偏差。

## 5. 总体实施路线

本轮建议分四个连续阶段推进。

### Phase A：先修评测与观测

目标：让指标能真实反映系统质量。

核心动作：

1. 将 benchmark 从“关键词包含判分”升级为“结构化断言判分”。
2. 保留旧的 `legacy keyword judge`，用于历史对比。
3. 新增 `structured judge` 作为主口径。
4. 每题落盘更完整的调试数据：
   - query plan
   - retrieved facts
   - retrieved chunks
   - calculation trace
   - final answer
   - judge breakdown

预期收益：

- `BQ-001`、`BQ-008` 这类误伤题可以被正确识别。
- 后续优化能看清到底是 planner、retrieval、calculator 还是 judge 的问题。

### Phase B：重构 Query Planner 的计算表达能力

目标：避免 planner 把问题意图“翻译错”。

核心动作：

1. 为 `QueryPlan` 引入显式计算规格，而不是只靠 `required_concepts` 隐式推理。
2. 增加下列结构化字段：
   - `calculation_intent`
   - `calculation_operands`
   - `requires_step_trace`
   - `answer_shape`
3. margin 类问题改为显式模板：
   - `gross margin = GrossProfit / Revenues`
   - `operating margin = OperatingIncomeLoss / Revenues`
4. 多季度、多年份比较一律拆成 period-aware sub-queries，再做统一汇总。
5. 文本 + 数字复合问题拆成 narrative lane 与 numeric lane 两路目标。

预期收益：

- 修复 `BQ-005`
- 为 `BQ-002`、`BQ-007` 的后续计算输出打下稳定基础

### Phase C：补足 Fact Resolution 与 Table Fallback

目标：当 authoritative facts 不完整时，系统仍能以可控方式给出 grounded 结果或 limitation。

核心动作：

1. 建立概念解析优先级：
   - 直接 XBRL fact
   - 衍生 fact
   - 表格回退值
   - limitation
2. 引入 concept family mapping，但只允许语义等价映射，不允许业务偷换。
3. 对 `CostOfRevenue` / `CostOfGoodsSold` 等 companyfacts 中已有概念建立映射规则。
4. 对“cost of automotive revenue”这类 segment-specific 概念优先走表格路径，而不是错误替换成 total cost。
5. 当 numeric lane 缺失但 narrative lane 有结果时，允许输出 partial grounded limitation，而不是整题短路失败。

预期收益：

- 改善 `BQ-003`
- 使系统对真实复杂问题更稳健，不再因为单一数字缺失就完全失效

### Phase D：稳定 Calculator 与 Answer Composer

目标：让结构化计算结果稳定、可解释、可验证。

核心动作：

1. ratio 方向由 `calculation_intent` 决定，不再依赖概念顺序。
2. 对 `requires_step_trace=True` 的问题优先走分解计算。
3. `Free Cash Flow` 在步骤模式下强制展示：
   - operating cash flow
   - capital expenditure
   - subtraction trace
   - final result
4. 统一 numeric answer 模板：
   - comparison 问题必须输出两端原值和变化结果
   - single lookup 问题必须输出明确结果位
   - ranking 问题必须输出各 period 值和胜出 period
5. limitation 文本统一分类：
   - missing concept
   - missing period
   - missing table evidence
   - semantic incompatibility

预期收益：

- 修复 `BQ-002`
- 修复 `BQ-007`
- 降低 `BQ-001`、`BQ-008` 因模板漂移带来的不稳定性

## 6. 建议的类型与接口扩展

### 6.1 QueryPlan

建议新增：

- `calculation_intent`
- `calculation_operands`
- `requires_step_trace`
- `answer_shape`
- `table_required_concepts`

这样可以让 planner、retrieval、calculator、answer composer 之间共享同一份明确的意图表达。

### 6.2 BenchmarkQuestion

建议保留旧字段：

- `expected_answer_contains`

同时新增结构化字段：

- `expected_status`
- `expected_facts`
- `expected_calc`
- `expected_period_semantics`

这样可以保留历史数据兼容性，同时让新的主评测口径更可靠。

### 6.3 QuestionResult

建议新增：

- `legacy_passed`
- `structured_passed`
- `judge_breakdown`

这样既能保留横向对比能力，也能帮助定位失败到底是系统问题还是 judge 问题。

## 7. 2-4 周实施计划

### Week 1：评测与诊断改造

目标：建立可信指标。

任务：

1. 重构 benchmark question schema
2. 引入 `structured judge`
3. 保留 `legacy judge`
4. 为每题保存完整 judge breakdown
5. 迁移现有 9 题到双轨评测

完成标准：

- `BQ-001`、`BQ-008` 可在 structured judge 下正确通过
- 每道题都能输出清晰的失败归因

### Week 2：Planner 与 Calculation Spec 改造

目标：修复 query intent 结构错误。

任务：

1. 扩展 `QueryPlan`
2. 实现 margin / comparison / ranking / step-trace intent
3. 修复 `operating margin` 的伪概念问题
4. 多季度查询统一拆 sub-query

完成标准：

- `BQ-005` 不再查找 `custom:OperatingMarginPercent`
- `BQ-002`、`BQ-005` 的 plan 可人工解释且结构稳定

### Week 3：Fact Resolution 与 Table Fallback

目标：提升 composite 问题处理能力。

任务：

1. 建立概念映射层
2. 为缺失 facts 的问题接入 table fallback
3. 实现 partial grounded limitation
4. 重点解决 `BQ-003`

完成标准：

- `BQ-003` 至少能输出有根据的 composite limitation
- 若表格证据足够，则可输出 narrative + numeric 的组合答案

### Week 4：Composer 收敛与回归

目标：统一最终输出行为并接受新 baseline。

任务：

1. 统一 calculation trace 输出模板
2. 统一 numeric answer 模板
3. 跑全量 benchmark 回归
4. 更新 failure analyses
5. 接受新的 structured baseline

完成标准：

- structured benchmark 达到 `>= 7/9`
- legacy benchmark 不低于当前重跑结果
- 所有失败题都能明确归因

## 8. 测试与验证建议

### 8.1 单元测试

应覆盖：

- margin 查询生成正确 operands
- `operating margin` 不再映射到伪概念
- `requires_step_trace=True` 时优先分解计算
- table fallback 只在 facts 缺失时触发
- partial limitation 不会丢掉已找到的一侧证据

### 8.2 集成测试

建议围绕当前核心题建立稳定回归：

- `BQ-002`: gross margin 正确计算与 trace
- `BQ-003`: text + numeric composite
- `BQ-005`: multi-quarter operating margin ranking
- `BQ-007`: free cash flow 分解步骤
- `BQ-001` / `BQ-008`: structured judge 正确通过

### 8.3 基线管理

建议同时保留两种分数：

1. `legacy score`
2. `structured score`

其中：

- `legacy score` 用于与历史结果对比
- `structured score` 作为新的主准确率指标

## 9. 本轮不建议优先做的事情

以下事项本轮不建议作为主线：

1. 先做中英双语问答能力
2. 先重做 Streamlit UI
3. 先更换更大的 LLM
4. 先引入复杂的 agent / MCP 工作流

原因是这些方向都不能优先解决当前最主要的准确率瓶颈。

## 10. 结论

当前 Tesla FinRAG 的准确率优化，应被视为一个“系统工程问题”，而不是一个单纯的“模型算法问题”。

如果只从算法角度思考，容易把问题归因于 embedding、reranker 或更强的生成模型；但结合当前代码与 baseline，可以更清楚地看到：

- planner 的结构表达
- calculator 的显式运算
- answer composer 的结果模板
- evaluation 的判分口径

这四层共同决定了当前准确率上限。

因此，本轮最合理的路线不是“换模型”，而是：

1. 先让评测可信
2. 再让 planner 表达正确
3. 再让 facts / tables 的解析与回退路径完整
4. 最后统一 calculator 与 answer composer 的输出行为

在这条路线下，准确率提升会更慢一点，但会更真实、更稳、更可持续，也更适合作为后续双语能力和更复杂问答能力的基础。
