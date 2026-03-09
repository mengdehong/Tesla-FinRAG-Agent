## Context

当前 Tesla FinRAG 系统的 pipeline 为：`QueryPlanner → HybridRetrieval → EvidenceLinker → StructuredCalculator → GroundedAnswerComposer`。评测由 `EvaluationRunner` 执行，判分逻辑为纯关键词包含匹配（`_check_answer` 检查 `expected_answer_contains` 列表）。

当前存在两类交叉问题：

1. **评测口径不可靠**：事实正确的答案因措辞不同被判失败（BQ-001, BQ-008），导致优化方向偏移。
2. **Planner 意图表达缺失**：`QueryPlan` 只有 `required_concepts` 和 `needs_calculation` 两个信号，`GroundedAnswerComposer._run_calculations` 用 `len(required_concepts)` 分支判断分子分母方向，margin 类问题被映射到不存在的伪概念（`custom:OperatingMarginPercent`）。

这两个问题相互关联：如果不先修评测，无法准确评估 Planner 修复的效果；如果不修 Planner，评测改进后仍会看到大量真实失败。

## Goals / Non-Goals

**Goals:**

- 建立结构化断言评测体系，能区分"事实正确但措辞不同"和"核心事实错误"
- 保留旧关键词判分作为 legacy 对比口径，确保历史可比性
- 为 QueryPlan 引入显式计算意图（calculation_intent + operands），消除对概念列表顺序的隐式依赖
- 修复 margin 类问题的伪概念映射，使 BQ-005 的 plan 正确产生 ratio intent
- 修改 Composer 使其消费新的 calculation_intent 来路由计算，而非依赖 `len(required_concepts)` 分支

**Non-Goals:**

- 本轮不引入 LLM-based judge（保持确定性评测）
- 本轮不更换更大的生成模型或引入 agent orchestration
- 本轮不修改检索层（HybridRetrieval）或存储层（LanceDB）
- 本轮不处理 Fact Resolution / Table Fallback（留给后续 Phase C+D change）
- 本轮不做中英双语支持

## Decisions

### 1. 双轨判分架构

`EvaluationRunner` 同时执行两种判分：

- **Legacy judge**：保持现有 `_check_answer` 逻辑不变（关键词包含）
- **Structured judge**：新增 `_structured_check` 方法，基于 `expected_status`、`expected_facts`、`expected_calc` 进行断言

`QuestionResult.passed` 由 structured judge 决定（新主口径），`legacy_passed` 保留旧判分结果。

**替代方案考虑**：直接替换旧 judge。拒绝原因：无法与历史 baseline 对比，且迁移期间无法验证新 judge 是否过于宽松。

### 2. 结构化断言模型设计

`BenchmarkQuestion` 新增字段均为 Optional，保持 JSON schema 向后兼容：

```
expected_status: AnswerStatus | None        # 期望的答案状态
expected_facts: list[str]                   # 期望命中的 XBRL concepts
expected_calc: ExpectedCalc | None          # 数值断言（operation + expected_value + tolerance）
expected_period_semantics: dict[str, str]   # 期望的时间语义分类
```

`ExpectedCalc` 支持的 operation 类型：`lookup`（直接查找）、`ratio`（比率）、`pct_change`（百分比变化）、`difference`（差值）、`rank`（排序）。

数值验证使用相对容差（`tolerance`），默认 0.01（1%），避免因浮点精度和四舍五入差异产生误判。

**替代方案考虑**：使用 LLM judge 进行语义匹配。拒绝原因：引入非确定性，不适合当前阶段对稳定性的要求。

### 3. QueryPlan 计算意图扩展

在 `QueryPlan` 上新增以下字段（均为 Optional + default，不破坏现有代码）：

- `calculation_intent: CalculationIntent | None` — 枚举：LOOKUP / RATIO / DIFFERENCE / PCT_CHANGE / RANK / STEP_TRACE
- `calculation_operands: list[CalculationOperand]` — 每个 operand 有 concept、role（numerator/denominator/minuend/subtrahend/target）、period
- `requires_step_trace: bool` — 是否需要分解计算步骤
- `answer_shape: AnswerShape` — 枚举：SINGLE_VALUE / COMPARISON / RANKING / COMPOSITE

**替代方案考虑**：将计算意图内嵌到 SubQuery 中。拒绝原因：计算意图是整个问题的属性，不是单个子查询的属性（例如 ratio 的分子分母可能来自同一 period 的不同 concept）。

### 4. 伪概念映射替换策略

当前 `_METRIC_ALIASES` 中存在：
- `custom:GrossMarginPercent` → aliases: ["gross margin %", "gross margin percent", ...]
- `custom:OperatingMarginPercent` → aliases: ["operating margin", "operating margin %", ...]

这些概念在 fact store 中不存在，且将 ratio 语义压缩成了单一概念查找。

替换方案：
1. 从 `_METRIC_ALIASES` 中移除 `custom:GrossMarginPercent` 和 `custom:OperatingMarginPercent`
2. 在 `RuleBasedQueryPlanner.plan()` 中新增 margin 检测规则：当检测到 "gross margin" / "operating margin" 等关键词时，生成 ratio intent + 对应的分子分母 operands
3. `required_concepts` 填入实际的分子分母概念（如 `OperatingIncomeLoss` + `Revenues`）

### 5. Composer 计算路由重构

当前 `GroundedAnswerComposer._run_calculations` 的分支逻辑为：
- `len(required_concepts) == 1` → period-over-period / lookup / rank
- `len(required_concepts) == 2` → ratio（concepts[0] 为分子，concepts[1] 为分母）

改为：
- 如果 `plan.calculation_intent` 存在，优先按 intent 路由
- 从 `plan.calculation_operands` 中提取分子分母，不依赖列表顺序
- 回退：如果 `calculation_intent` 为 None，保持现有逻辑（兼容性）

## Risks / Trade-offs

- [Risk] Structured judge 可能过于宽松，导致虚假通过 → Mitigation: 同时保留 legacy judge，人工审核结构化断言定义，确保 tolerance 合理
- [Risk] QueryPlan 字段扩展可能与 frozen model 冲突 → Mitigation: 新字段均有默认值，Pydantic frozen model 在构造时设置，不影响
- [Risk] Margin 检测规则可能误判非 margin 问题 → Mitigation: 使用精确的 regex pattern 匹配 "gross margin" / "operating margin" 后跟随的问题语境
- [Risk] Composer 的 intent 路由改动可能影响已经通过的 BQ-001/BQ-008/BQ-009 → Mitigation: 回退兼容逻辑保留，intent 为 None 时走旧路径

## Migration Plan

1. 所有模型扩展使用 Optional + default，确保现有代码不受影响
2. `EvaluationRunner` 先实现双轨判分，`passed` 仍由 legacy judge 决定
3. 验证 structured judge 对 9 题的判分结果后，切换 `passed` 的主口径
4. 迁移 `benchmark_questions.json` 为双轨格式
5. 跑全量评测对比 legacy vs structured 结果

## Open Questions

- BQ-003（text + table composite）的 structured 断言如何定义？当前 Phase A+B 中暂定为宽松断言（仅检查 status + 部分 facts），Phase C+D 中再细化
