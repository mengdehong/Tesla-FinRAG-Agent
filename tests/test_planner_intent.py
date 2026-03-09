"""Tests for Phase B planner restructuring: intent inference, margin detection,
step-trace detection, answer shape inference, and calculation operand building.

Covers Tasks 5.1-5.5 of the accuracy optimization change.
"""

from __future__ import annotations

from datetime import date

import pytest

from tesla_finrag.models import (
    AnswerShape,
    CalculationIntent,
    CalculationOperand,
    QueryLanguage,
)
from tesla_finrag.planning.query_planner import (
    RuleBasedQueryPlanner,
    _build_operands_for_intent,
    _detect_step_trace,
    _infer_answer_shape,
    _infer_calculation_intent,
    _infer_margin_intent,
    detect_query_language,
    extract_metrics,
    extract_periods,
)

# ===================================================================
# 1. Pseudo-concept removal (Task 5.1)
# ===================================================================


class TestPseudoConceptRemoval:
    """Verify custom:GrossMarginPercent and custom:OperatingMarginPercent
    are no longer produced by extract_metrics."""

    def test_gross_margin_percent_not_custom(self):
        """'gross margin %' should map to us-gaap:GrossProfit, not custom:GrossMarginPercent."""
        metrics = extract_metrics("What was the gross margin % in FY2023?")
        assert "custom:GrossMarginPercent" not in metrics
        assert "us-gaap:GrossProfit" in metrics

    def test_gross_margin_percentage_not_custom(self):
        metrics = extract_metrics("Show gross margin percentage for FY2022")
        assert "custom:GrossMarginPercent" not in metrics
        assert "us-gaap:GrossProfit" in metrics

    def test_operating_margin_not_custom(self):
        """'operating margin' should map to us-gaap:OperatingIncomeLoss."""
        metrics = extract_metrics("What was the operating margin in FY2023?")
        assert "custom:OperatingMarginPercent" not in metrics
        assert "us-gaap:OperatingIncomeLoss" in metrics

    def test_operating_margin_percent_not_custom(self):
        metrics = extract_metrics("Show the operating margin % for Q3 2023")
        assert "custom:OperatingMarginPercent" not in metrics
        assert "us-gaap:OperatingIncomeLoss" in metrics

    def test_plain_gross_profit_still_works(self):
        """Ensure 'gross profit' still maps correctly."""
        metrics = extract_metrics("What was the gross profit in FY2023?")
        assert "us-gaap:GrossProfit" in metrics

    def test_operating_income_still_works(self):
        """Ensure 'operating income' still maps correctly."""
        metrics = extract_metrics("Show operating income for FY2022")
        assert "us-gaap:OperatingIncomeLoss" in metrics

    def test_chinese_revenue_metric_supported(self):
        metrics = extract_metrics("比较 FY2022 和 FY2023 的总营收，同比增长率是多少？")
        assert metrics == ["us-gaap:Revenues"]

    def test_chinese_margin_metric_supported(self):
        metrics = extract_metrics("特斯拉2023财年的毛利率是多少？")
        assert "us-gaap:GrossProfit" in metrics


# ===================================================================
# 2. Margin detection (Task 5.2)
# ===================================================================


class TestMarginDetection:
    """Test _infer_margin_intent for gross and operating margin queries."""

    def test_gross_margin_ratio_intent(self):
        intent, operands, metrics = _infer_margin_intent(
            "What was Tesla's gross profit margin for FY2023?",
            ["us-gaap:GrossProfit"],
            [date(2023, 12, 31)],
        )
        assert intent == CalculationIntent.RATIO
        assert len(operands) == 2
        assert operands[0].concept == "us-gaap:GrossProfit"
        assert operands[0].role == "numerator"
        assert operands[1].concept == "us-gaap:Revenues"
        assert operands[1].role == "denominator"

    def test_gross_margin_augments_metrics(self):
        """When 'gross margin' is detected, Revenues should be added to metrics."""
        _, _, metrics = _infer_margin_intent(
            "What was the gross margin?",
            ["us-gaap:GrossProfit"],
            [],
        )
        assert "us-gaap:Revenues" in metrics
        assert "us-gaap:GrossProfit" in metrics

    def test_operating_margin_ratio_intent(self):
        intent, operands, metrics = _infer_margin_intent(
            "What was Tesla's operating margin in FY2023?",
            ["us-gaap:OperatingIncomeLoss"],
            [date(2023, 12, 31)],
        )
        assert intent == CalculationIntent.RATIO
        assert operands[0].concept == "us-gaap:OperatingIncomeLoss"
        assert operands[0].role == "numerator"
        assert operands[1].concept == "us-gaap:Revenues"
        assert operands[1].role == "denominator"

    def test_no_margin_no_intent(self):
        """Revenue-only question should not trigger margin intent."""
        intent, operands, metrics = _infer_margin_intent(
            "What was Tesla's revenue in FY2023?",
            ["us-gaap:Revenues"],
            [date(2023, 12, 31)],
        )
        assert intent is None
        assert operands == []
        assert metrics == ["us-gaap:Revenues"]

    def test_margin_multi_period_ranking_suppressed(self):
        """Margin ranking should preserve ratio operands and use RANK intent."""
        intent, operands, metrics = _infer_margin_intent(
            "Which quarter had the highest gross margin in Q1, Q2, Q3 2023?",
            ["us-gaap:GrossProfit"],
            [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)],
        )
        assert intent == CalculationIntent.RANK
        assert len(operands) == 6
        assert {o.role for o in operands} == {"numerator", "denominator"}
        assert "us-gaap:Revenues" in metrics  # metrics still augmented

    def test_margin_multi_period_no_ranking_keeps_ratio(self):
        """Margin with multiple periods but no ranking keywords should keep RATIO."""
        intent, operands, metrics = _infer_margin_intent(
            "Compare gross margin between FY2022 and FY2023",
            ["us-gaap:GrossProfit"],
            [date(2022, 12, 31), date(2023, 12, 31)],
        )
        assert intent == CalculationIntent.RATIO
        assert len(operands) == 4  # 2 pairs x 2 periods

    def test_margin_no_period_operands(self):
        """Margin without period should produce period-agnostic operands."""
        intent, operands, _ = _infer_margin_intent(
            "What is the gross margin?",
            ["us-gaap:GrossProfit"],
            [],
        )
        assert intent == CalculationIntent.RATIO
        assert len(operands) == 2
        assert operands[0].period is None
        assert operands[1].period is None

    def test_no_double_add_metrics(self):
        """If Revenues already in metrics, margin detection shouldn't add duplicate."""
        _, _, metrics = _infer_margin_intent(
            "What was the gross margin?",
            ["us-gaap:GrossProfit", "us-gaap:Revenues"],
            [],
        )
        assert metrics.count("us-gaap:Revenues") == 1


# ===================================================================
# 3. Step-trace detection (Task 5.3)
# ===================================================================


class TestStepTraceDetection:
    """Test _detect_step_trace for various phrasings."""

    @pytest.mark.parametrize(
        "question",
        [
            "Show each step of the calculation",
            "Show how gross profit divided by revenue produces the margin",
            "Walk me through the free cash flow calculation",
            "Show the step-by-step breakdown",
            "Give me a breakdown of the calculation",
            "Explain the calculation for operating margin",
            "Show the full calculation",
        ],
    )
    def test_step_trace_detected(self, question: str):
        assert _detect_step_trace(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "What was Tesla's revenue in FY2023?",
            "Compare revenue between FY2022 and FY2023",
            "Which quarter had the highest operating margin?",
        ],
    )
    def test_step_trace_not_detected(self, question: str):
        assert _detect_step_trace(question) is False

    @pytest.mark.parametrize(
        "question",
        [
            "请展示毛利润除以总营收的计算过程。",
            "请逐步说明自由现金流的计算过程。",
        ],
    )
    def test_step_trace_detected_for_chinese(self, question: str):
        assert _detect_step_trace(question) is True


# ===================================================================
# 4. Answer shape inference (Task 5.4)
# ===================================================================


class TestAnswerShapeInference:
    """Test _infer_answer_shape rules."""

    def test_ranking_with_multi_period(self):
        shape = _infer_answer_shape(
            "Which quarter had the highest revenue?",
            [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)],
            ["us-gaap:Revenues"],
        )
        assert shape == AnswerShape.RANKING

    def test_ranking_keyword_with_two_periods(self):
        shape = _infer_answer_shape(
            "Which year had the highest revenue: 2022 or 2023?",
            [date(2022, 12, 31), date(2023, 12, 31)],
            ["us-gaap:Revenues"],
        )
        assert shape == AnswerShape.RANKING

    def test_comparison_two_periods(self):
        shape = _infer_answer_shape(
            "Compare revenue between FY2022 and FY2023",
            [date(2022, 12, 31), date(2023, 12, 31)],
            ["us-gaap:Revenues"],
        )
        assert shape == AnswerShape.COMPARISON

    def test_three_periods_one_metric_is_ranking(self):
        """3+ periods with a single metric defaults to RANKING."""
        shape = _infer_answer_shape(
            "Show Tesla's revenue for 2021, 2022, and 2023",
            [date(2021, 12, 31), date(2022, 12, 31), date(2023, 12, 31)],
            ["us-gaap:Revenues"],
        )
        assert shape == AnswerShape.RANKING

    def test_composite_narrative_multi_metric(self):
        shape = _infer_answer_shape(
            "Describe the revenue and cost trends mentioned in the filings",
            [date(2023, 12, 31)],
            ["us-gaap:Revenues", "us-gaap:CostOfGoodsAndServicesSold"],
        )
        assert shape == AnswerShape.COMPOSITE

    def test_single_value_default(self):
        shape = _infer_answer_shape(
            "What was Tesla's revenue in FY2023?",
            [date(2023, 12, 31)],
            ["us-gaap:Revenues"],
        )
        assert shape == AnswerShape.SINGLE_VALUE

    def test_no_metrics_no_periods(self):
        shape = _infer_answer_shape(
            "What does Tesla do?",
            [],
            [],
        )
        assert shape == AnswerShape.SINGLE_VALUE


# ===================================================================
# 5. Calculation intent inference (Task 5.5)
# ===================================================================


class TestCalculationIntentInference:
    """Test _infer_calculation_intent rules."""

    def test_pct_change_growth_rate(self):
        intent = _infer_calculation_intent(
            "What was the year-over-year growth rate?",
            ["us-gaap:Revenues"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.PCT_CHANGE

    def test_pct_change_grew(self):
        intent = _infer_calculation_intent(
            "How much did revenue grow from 2022 to 2023?",
            ["us-gaap:Revenues"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.PCT_CHANGE

    def test_ratio_two_metrics(self):
        intent = _infer_calculation_intent(
            "What is the ratio of gross profit to revenue?",
            ["us-gaap:GrossProfit", "us-gaap:Revenues"],
            [date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.RATIO

    def test_rank_multi_period(self):
        intent = _infer_calculation_intent(
            "Which quarter had the highest operating income?",
            ["us-gaap:OperatingIncomeLoss"],
            [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.RANK

    def test_difference_two_periods(self):
        intent = _infer_calculation_intent(
            "What was the difference in net income between 2022 and 2023?",
            ["us-gaap:NetIncomeLoss"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.DIFFERENCE

    def test_lookup_single_metric_period(self):
        intent = _infer_calculation_intent(
            "What was Tesla's revenue in FY2023?",
            ["us-gaap:Revenues"],
            [date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.LOOKUP

    def test_no_metrics_returns_none(self):
        intent = _infer_calculation_intent(
            "What risks did Tesla mention?",
            [],
            [date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent is None

    def test_margin_intent_takes_precedence(self):
        """Margin intent should override any other inference."""
        intent = _infer_calculation_intent(
            "What was the growth rate of gross margin?",
            ["us-gaap:GrossProfit", "us-gaap:Revenues"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            margin_intent=CalculationIntent.RATIO,
        )
        assert intent == CalculationIntent.RATIO

    def test_pct_change_chinese(self):
        intent = _infer_calculation_intent(
            "比较 FY2022 和 FY2023 的总营收，同比增长率是多少？",
            ["us-gaap:Revenues"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            margin_intent=None,
        )
        assert intent == CalculationIntent.PCT_CHANGE


# ===================================================================
# 6. Operand building
# ===================================================================


class TestOperandBuilding:
    """Test _build_operands_for_intent."""

    def test_pct_change_operands(self):
        operands = _build_operands_for_intent(
            CalculationIntent.PCT_CHANGE,
            ["us-gaap:Revenues"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            existing_operands=[],
        )
        assert len(operands) == 2
        assert operands[0].role == "base"
        assert operands[0].period == date(2022, 12, 31)
        assert operands[1].role == "target"
        assert operands[1].period == date(2023, 12, 31)

    def test_ratio_operands(self):
        operands = _build_operands_for_intent(
            CalculationIntent.RATIO,
            ["us-gaap:GrossProfit", "us-gaap:Revenues"],
            [date(2023, 12, 31)],
            existing_operands=[],
        )
        assert len(operands) == 2
        assert operands[0].role == "numerator"
        assert operands[1].role == "denominator"

    def test_rank_operands(self):
        periods = [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)]
        operands = _build_operands_for_intent(
            CalculationIntent.RANK,
            ["us-gaap:OperatingIncomeLoss"],
            periods,
            existing_operands=[],
        )
        assert len(operands) == 3
        assert all(o.role == "primary" for o in operands)
        assert [o.period for o in operands] == sorted(periods)

    def test_lookup_operands(self):
        operands = _build_operands_for_intent(
            CalculationIntent.LOOKUP,
            ["us-gaap:Revenues"],
            [date(2023, 12, 31)],
            existing_operands=[],
        )
        assert len(operands) == 1
        assert operands[0].role == "primary"
        assert operands[0].period == date(2023, 12, 31)

    def test_existing_operands_preserved(self):
        """When existing_operands is non-empty, they should be returned as-is."""
        existing = [CalculationOperand(concept="us-gaap:GrossProfit", role="numerator")]
        operands = _build_operands_for_intent(
            CalculationIntent.RATIO,
            ["us-gaap:GrossProfit", "us-gaap:Revenues"],
            [date(2023, 12, 31)],
            existing_operands=existing,
        )
        assert operands is existing

    def test_none_intent_no_operands(self):
        operands = _build_operands_for_intent(
            None,
            ["us-gaap:Revenues"],
            [date(2023, 12, 31)],
            existing_operands=[],
        )
        assert operands == []

    def test_difference_operands(self):
        operands = _build_operands_for_intent(
            CalculationIntent.DIFFERENCE,
            ["us-gaap:NetIncomeLoss"],
            [date(2022, 12, 31), date(2023, 12, 31)],
            existing_operands=[],
        )
        assert len(operands) == 2
        assert operands[0].role == "base"
        assert operands[1].role == "target"


# ===================================================================
# 7. Full planner integration (Task 5.1-5.5 combined)
# ===================================================================


class TestPlannerIntentIntegration:
    """End-to-end tests: full planner produces correct intent fields."""

    def test_bq001_revenue_yoy_growth(self):
        """BQ-001: cross-year revenue comparison → PCT_CHANGE + COMPARISON."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan(
            "Compare Tesla's total revenue between FY2022 and FY2023. "
            "What was the year-over-year growth rate?"
        )
        assert plan.calculation_intent == CalculationIntent.PCT_CHANGE
        assert plan.answer_shape == AnswerShape.COMPARISON
        assert plan.requires_step_trace is False
        assert len(plan.calculation_operands) == 2
        assert plan.calculation_operands[0].role == "base"
        assert plan.calculation_operands[1].role == "target"

    def test_bq002_gross_margin_with_step_trace(self):
        """BQ-002: gross profit margin with 'show how' → RATIO + step trace."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan(
            "What was Tesla's gross profit margin for FY2023? "
            "Show how gross profit divided by total revenue produces the margin percentage."
        )
        assert plan.calculation_intent == CalculationIntent.RATIO
        assert plan.requires_step_trace is True
        assert "us-gaap:GrossProfit" in plan.required_concepts
        assert "us-gaap:Revenues" in plan.required_concepts
        # Operands should have numerator/denominator
        numerators = [o for o in plan.calculation_operands if o.role == "numerator"]
        denominators = [o for o in plan.calculation_operands if o.role == "denominator"]
        assert len(numerators) >= 1
        assert len(denominators) >= 1
        assert numerators[0].concept == "us-gaap:GrossProfit"
        assert denominators[0].concept == "us-gaap:Revenues"

    def test_bq005_operating_margin_ranking(self):
        """BQ-005: multi-quarter operating margin ranking → RANK + RANKING.

        Even though 'operating margin' is mentioned, the plan should rank the
        derived margin ratio rather than raw operating income.
        """
        planner = RuleBasedQueryPlanner()
        plan = planner.plan(
            "Compare Tesla's operating income across Q1 2023, Q2 2023, and Q3 2023. "
            "Which quarter had the highest operating margin?"
        )
        assert plan.calculation_intent == CalculationIntent.RANK
        assert plan.answer_shape == AnswerShape.RANKING
        assert "us-gaap:OperatingIncomeLoss" in plan.required_concepts
        assert "us-gaap:Revenues" in plan.required_concepts
        assert "custom:OperatingMarginPercent" not in plan.required_concepts
        numerators = [o for o in plan.calculation_operands if o.role == "numerator"]
        denominators = [o for o in plan.calculation_operands if o.role == "denominator"]
        assert len(numerators) == 3
        assert len(denominators) == 3
        assert {o.period for o in numerators} == {
            date(2023, 3, 31),
            date(2023, 6, 30),
            date(2023, 9, 30),
        }

    def test_bq007_fcf_step_trace(self):
        """BQ-007: FCF calculation with decomposition → STEP_TRACE intent."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan(
            "Calculate Tesla's free cash flow for FY2023 by subtracting "
            "capital expenditures from operating cash flow. Show each step."
        )
        assert plan.calculation_intent == CalculationIntent.STEP_TRACE
        assert plan.requires_step_trace is True
        assert plan.needs_calculation is True
        assert "custom:FreeCashFlow" in plan.required_concepts
        assert "custom:CapitalExpenditure" in plan.required_concepts
        assert "us-gaap:NetCashProvidedByUsedInOperatingActivities" in plan.required_concepts
        assert {o.role for o in plan.calculation_operands} == {
            "minuend",
            "subtrahend",
            "result",
        }


class TestChinesePlannerSupport:
    def test_detect_query_language(self):
        assert detect_query_language("What was Tesla revenue in FY2023?") == QueryLanguage.ENGLISH
        assert detect_query_language("特斯拉2023财年的营收是多少？") == QueryLanguage.CHINESE
        assert detect_query_language("比较 Tesla FY2022 和 FY2023 的总营收") == QueryLanguage.MIXED

    def test_extract_periods_supports_chinese_fiscal_year_and_quarters(self):
        periods = extract_periods("比较2023年Q1、Q2、Q3的营业利润，哪个季度最高？")
        assert periods == [
            date(2023, 3, 31),
            date(2023, 6, 30),
            date(2023, 9, 30),
        ]

    def test_extract_periods_supports_chinese_as_of_date(self):
        periods = extract_periods("截至2023年12月31日，特斯拉的现金及现金等价物是多少？")
        assert periods == [date(2023, 12, 31)]

    def test_planner_normalizes_chinese_numeric_query(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("比较特斯拉FY2022和FY2023的总营收，同比增长率是多少？")
        assert plan.query_language == QueryLanguage.MIXED
        assert plan.calculation_intent == CalculationIntent.PCT_CHANGE
        assert plan.answer_shape == AnswerShape.COMPARISON
        assert plan.required_periods == [date(2022, 12, 31), date(2023, 12, 31)]
        assert plan.required_concepts == ["us-gaap:Revenues"]
        assert "revenue" in plan.normalized_query
        assert "growth rate" in plan.normalized_query
        assert len(plan.sub_queries) == 2
        assert all("FY202" in sq.search_text for sq in plan.sub_queries)

    def test_planner_normalizes_chinese_narrative_query(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("2023年10-K里提到了哪些供应链风险因素？")
        assert plan.query_language == QueryLanguage.MIXED
        assert plan.query_type.value == "narrative_compare"
        assert plan.required_periods == [date(2023, 12, 31)]
        assert plan.required_concepts == []
        assert "supply chain" in plan.normalized_query
        assert "risk factors" in plan.normalized_query

    def test_planner_handles_chinese_margin_ranking(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("比较2023年Q1、Q2、Q3的营业利润，哪个季度营业利润率最高？")
        assert plan.calculation_intent == CalculationIntent.RANK
        assert plan.answer_shape == AnswerShape.RANKING
        assert "us-gaap:OperatingIncomeLoss" in plan.required_concepts
        assert "us-gaap:Revenues" in plan.required_concepts
        assert len([o for o in plan.calculation_operands if o.role == "numerator"]) == 3

    def test_bq008_simple_revenue_lookup(self):
        """BQ-008: simple revenue lookup → LOOKUP + SINGLE_VALUE."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What was Tesla's total revenue in FY2023?")
        assert plan.calculation_intent == CalculationIntent.LOOKUP
        assert plan.answer_shape == AnswerShape.SINGLE_VALUE
        assert plan.requires_step_trace is False

    def test_narrative_question_no_intent(self):
        """Narrative-only question → no calculation intent."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What supply chain risks did Tesla discuss in 2023?")
        assert plan.calculation_intent is None
        assert plan.requires_step_trace is False

    def test_needs_calculation_set_by_intent(self):
        """Even without explicit calc keywords, inferred intent sets needs_calculation."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What was Tesla's EPS in FY2023?")
        assert plan.calculation_intent == CalculationIntent.LOOKUP
        assert plan.needs_calculation is True

    def test_step_trace_sets_needs_calculation(self):
        """Step-trace detection should also enable needs_calculation."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("Explain the calculation of Tesla's gross margin. Show each step.")
        assert plan.requires_step_trace is True
        assert plan.needs_calculation is True
