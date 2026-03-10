# Tasks: Phase C — Fact Resolution & Table Fallback

## T-001: 复合问题检测 (Planner)
- [x] 在 `query_planner.py` 中检测 composite question pattern
- [x] 为 narrative lane 生成独立 sub-query（human-readable 搜索词）
- [x] 确保 `answer_shape = COMPOSITE` 被正确设置

## T-002: Table Fallback (Linker)
- [x] 在 `linker.py` 中添加 `_try_table_fallback()` 方法
- [x] 当 required concept 在 fact store 中缺失时，扫描 table chunks
- [x] 从 table chunk 中提取匹配的数值，创建 table-backed FactRecord
- [x] 添加 provenance metadata 标记 fact 来源为 table

## T-003: Composite Partial Answer (Composer)
- [x] 修改 `composer.py` 中 evidence sufficiency check 逻辑
- [x] 对 COMPOSITE answer_shape，narrative lane 独立评估
- [x] 当 narrative 成功但 numeric 缺失时，返回 partial answer
- [x] Status = OK + limitation text 说明 numeric lane 数据不足

## T-004: 测试
- [x] Table fallback unit test (27 tests in `tests/test_phase_c.py`)
- [x] Composite partial answer test
- [x] Semantic protection test (automotive cost ≠ total cost)
- [x] Non-regression: 450 passed, 80 skipped, 0 failures

## T-005: Benchmark 验证
- [x] 无需调整 BQ-003 assertions — table fallback 成功创建 FactRecords
- [x] 运行完整 benchmark → 9/9 (100%) ✓
  - Run ID: `dd5d13e8ebab`
  - BQ-003: status=ok, table_fallback_count=2, is_composite=true
