## 1. 评测模型扩展 (Phase A - Models)

- [x] 1.1 在 `evaluation/models.py` 中新增 `ExpectedCalc` 模型（operation, numerator, denominator, expected_value, tolerance 字段）
- [x] 1.2 在 `evaluation/models.py` 中新增 `JudgeBreakdown` 模型（status_ok, facts_found, facts_missing, calc_correct, calc_detail 字段）
- [x] 1.3 扩展 `BenchmarkQuestion` 模型，添加 `expected_status`, `expected_facts`, `expected_calc`, `expected_period_semantics` 字段（均 Optional + default）
- [x] 1.4 扩展 `QuestionResult` 模型，添加 `legacy_passed`, `structured_passed`, `judge_breakdown` 字段，并添加 `retrieval_debug` 字段保存完整调试数据
- [x] 1.5 验证现有 `benchmark_questions.json` 能正常加载新模型（向后兼容性测试）

## 2. 结构化评测判分 (Phase A - Judge)

- [x] 2.1 将 `EvaluationRunner._check_answer` 重命名为 `_legacy_check`，保持原有逻辑不变
- [x] 2.2 实现 `EvaluationRunner._structured_check` 方法：状态断言 + 事实命中检查 + 数值容差验证
- [x] 2.3 修改 `_run_single` 方法，同时调用 legacy 和 structured judge，填充 `legacy_passed` 和 `structured_passed`
- [x] 2.4 实现 `passed` 字段的双轨决策逻辑：structured 可用时优先，否则回退 legacy
- [x] 2.5 为 structured judge 编写单元测试：覆盖 lookup/pct_change/ratio/rank 各 operation 类型，以及 facts_found/facts_missing 检查

## 3. 基准题迁移 (Phase A - Data)

- [x] 3.1 为 9 道基准题设计结构化断言字段（expected_status, expected_facts, expected_calc）
- [x] 3.2 更新 `benchmark_questions.json`，为每道题添加结构化断言字段
- [x] 3.3 验证 BQ-001 和 BQ-008 在 structured judge 下能正确通过（数值断言 + 状态断言）
- [x] 3.4 验证所有 9 题的 legacy judge 结果与更新前一致（回归验证）

## 4. QueryPlan 计算意图模型 (Phase B - Models)

- [x] 4.1 在 `models.py` 中新增 `CalculationIntent` 枚举（LOOKUP, RATIO, DIFFERENCE, PCT_CHANGE, RANK, STEP_TRACE）
- [x] 4.2 在 `models.py` 中新增 `AnswerShape` 枚举（SINGLE_VALUE, COMPARISON, RANKING, COMPOSITE）
- [x] 4.3 在 `models.py` 中新增 `CalculationOperand` 模型（concept, role, period 字段）
- [x] 4.4 扩展 `QueryPlan` 模型，添加 `calculation_intent`, `calculation_operands`, `requires_step_trace`, `answer_shape` 字段（均 Optional + default）

## 5. Query Planner 重构 (Phase B - Planner)

- [x] 5.1 从 `_METRIC_ALIASES` 中移除 `custom:GrossMarginPercent` 和 `custom:OperatingMarginPercent` 条目
- [x] 5.2 实现 margin 检测规则：检测 "gross margin" 时生成 ratio operands（GrossProfit / Revenues），检测 "operating margin" 时生成 ratio operands（OperatingIncomeLoss / Revenues）；ranking+multi-period 场景下生成 `RANK` intent，但保留按 period 的 numerator/denominator operands 以支持真实 margin 排名
- [x] 5.3 实现 step-trace 检测规则：检测 "show each step" / "show how" / "walk through" / "breakdown" 时设置 `requires_step_trace=True`
- [x] 5.4 实现 answer_shape 推断规则：ranking 关键词 + 多 period → RANKING，comparison 关键词 + 2 period → COMPARISON，3+ periods → RANKING，单 period → SINGLE_VALUE
- [x] 5.5 实现 calculation_intent 推断规则：PCT_CHANGE > RANK > RATIO > DIFFERENCE > LOOKUP（优先级顺序），margin_intent 优先
- [x] 5.6 为 Planner 编写单元测试（54 tests）：验证 BQ-005 不含 `custom:OperatingMarginPercent`，验证 margin 问题生成正确 operands，验证全部 benchmark 问题的 intent/shape

## 6. Composer 计算路由重构 (Phase B - Composer)

- [x] 6.1 在 `GroundedAnswerComposer._run_calculations` 中新增 intent 路由分支：优先使用 `plan.calculation_intent` 路由
- [x] 6.2 实现 RATIO intent 路由：从 `calculation_operands` 提取 numerator/denominator，调用 `compute_ratio`
- [x] 6.3 实现 PCT_CHANGE intent 路由：从 operands 提取 concept + periods，调用 `period_over_period`
- [x] 6.4 实现 RANK intent 路由：普通排序走单 concept `rank`，margin 排名场景使用 numerator/denominator operands 逐 period 计算 ratio 后再排序
- [x] 6.5 实现 DIFFERENCE intent 路由：从 operands 提取 concept + periods，调用 `period_over_period(as_percent=False)`
- [x] 6.6 实现 LOOKUP intent 路由：直接查找 fact 值
- [x] 6.7 保留 fallback 兼容：当 `calculation_intent is None` 时走现有 `len(required_concepts)` 分支（`_run_legacy_routing`）

## 7. 集成验证与回归 (Validation)

- [x] 7.1 运行 `uv run ruff check .` 确保代码风格一致 — All checks passed
- [x] 7.2 运行 `uv run pytest -q` 确保所有现有测试通过 — 420 passed, 80 skipped
- [x] 7.3 运行完整评测流程 `uv run python -m tesla_finrag.evaluation.runner`，使用 `PROCESSED_DATA_DIR`/`LANCEDB_URI` 指向主仓 processed artifacts，并设置 `OLLAMA_CHAT_MODEL=qwen2.5:7b`；结果 `8/9 = 88.89%`，仅 BQ-003 仍失败
- [x] 7.4 对比 legacy judge 和 structured judge 结果，确认 BQ-001/BQ-008 的误伤被消除：两题均表现为 `legacy=F, struct=P`
- [x] 7.5 验证 BQ-005 的 query plan 不再包含伪概念，且包含正确的 rank intent（非 ratio）
