from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.calculation.calculator import StructuredCalculator
from tesla_finrag.models import (
    AnswerShape,
    CalculationIntent,
    CalculationOperand,
    EvidenceBundle,
    FactRecord,
    QueryLanguage,
    QueryPlan,
    QueryType,
)


def _composer() -> GroundedAnswerComposer:
    composer = object.__new__(GroundedAnswerComposer)
    composer._calculator = StructuredCalculator()
    return composer


def _fact(concept: str, value: float, period_end: date, label: str) -> FactRecord:
    return FactRecord(
        doc_id=uuid4(),
        concept=concept,
        label=label,
        value=value,
        unit="USD",
        scale=1,
        period_end=period_end,
    )


def test_ratio_intent_compares_all_periods_instead_of_first_only() -> None:
    composer = _composer()
    plan = QueryPlan(
        original_query="Compare gross margin between FY2022 and FY2023",
        query_type=QueryType.NUMERIC_CALCULATION,
        required_periods=[date(2022, 12, 31), date(2023, 12, 31)],
        required_concepts=["us-gaap:GrossProfit", "us-gaap:Revenues"],
        needs_calculation=True,
        calculation_intent=CalculationIntent.RATIO,
        answer_shape=AnswerShape.COMPARISON,
        calculation_operands=[
            CalculationOperand(
                concept="us-gaap:GrossProfit",
                role="numerator",
                period=date(2022, 12, 31),
            ),
            CalculationOperand(
                concept="us-gaap:Revenues",
                role="denominator",
                period=date(2022, 12, 31),
            ),
            CalculationOperand(
                concept="us-gaap:GrossProfit",
                role="numerator",
                period=date(2023, 12, 31),
            ),
            CalculationOperand(
                concept="us-gaap:Revenues",
                role="denominator",
                period=date(2023, 12, 31),
            ),
        ],
    )
    facts = [
        _fact("us-gaap:GrossProfit", 10, date(2022, 12, 31), "Gross Profit"),
        _fact("us-gaap:Revenues", 100, date(2022, 12, 31), "Revenue"),
        _fact("us-gaap:GrossProfit", 30, date(2023, 12, 31), "Gross Profit"),
        _fact("us-gaap:Revenues", 100, date(2023, 12, 31), "Revenue"),
    ]

    result, trace = composer._run_calculations(plan, facts)

    assert result == pytest.approx(20.0)
    assert any("Percentage (2022-12-31): 10.00%" in line for line in trace)
    assert any("Percentage (2023-12-31): 30.00%" in line for line in trace)
    assert any("Change in ratio:" in line for line in trace)


def test_rank_intent_ranks_margin_not_raw_numerator() -> None:
    composer = _composer()
    periods = [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)]
    plan = QueryPlan(
        original_query="Which quarter had the highest operating margin?",
        query_type=QueryType.NUMERIC_CALCULATION,
        required_periods=periods,
        required_concepts=["us-gaap:OperatingIncomeLoss", "us-gaap:Revenues"],
        needs_calculation=True,
        calculation_intent=CalculationIntent.RANK,
        answer_shape=AnswerShape.RANKING,
        calculation_operands=[
            CalculationOperand(
                concept="us-gaap:OperatingIncomeLoss",
                role="numerator",
                period=periods[0],
            ),
            CalculationOperand(concept="us-gaap:Revenues", role="denominator", period=periods[0]),
            CalculationOperand(
                concept="us-gaap:OperatingIncomeLoss",
                role="numerator",
                period=periods[1],
            ),
            CalculationOperand(concept="us-gaap:Revenues", role="denominator", period=periods[1]),
            CalculationOperand(
                concept="us-gaap:OperatingIncomeLoss",
                role="numerator",
                period=periods[2],
            ),
            CalculationOperand(concept="us-gaap:Revenues", role="denominator", period=periods[2]),
        ],
    )
    facts = [
        _fact("us-gaap:OperatingIncomeLoss", 50, periods[0], "Operating Income"),
        _fact("us-gaap:Revenues", 100, periods[0], "Revenue"),
        _fact("us-gaap:OperatingIncomeLoss", 80, periods[1], "Operating Income"),
        _fact("us-gaap:Revenues", 400, periods[1], "Revenue"),
        _fact("us-gaap:OperatingIncomeLoss", 60, periods[2], "Operating Income"),
        _fact("us-gaap:Revenues", 100, periods[2], "Revenue"),
    ]

    result, trace = composer._run_calculations(plan, facts)

    assert result == pytest.approx(60.0)
    assert trace[0] == "Ranking derived ratio (highest to lowest):"
    assert "2023-09-30" in trace[1]
    assert "60.00%" in trace[1]


def test_step_trace_intent_decomposes_free_cash_flow() -> None:
    composer = _composer()
    period = date(2023, 12, 31)
    plan = QueryPlan(
        original_query=(
            "Calculate Tesla's free cash flow for FY2023 by subtracting capital "
            "expenditures from operating cash flow. Show each step."
        ),
        query_type=QueryType.NUMERIC_CALCULATION,
        required_periods=[period],
        required_concepts=[
            "custom:FreeCashFlow",
            "custom:CapitalExpenditure",
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        ],
        needs_calculation=True,
        calculation_intent=CalculationIntent.STEP_TRACE,
        requires_step_trace=True,
        calculation_operands=[
            CalculationOperand(
                concept="us-gaap:NetCashProvidedByUsedInOperatingActivities",
                role="minuend",
                period=period,
            ),
            CalculationOperand(
                concept="custom:CapitalExpenditure",
                role="subtrahend",
                period=period,
            ),
            CalculationOperand(
                concept="custom:FreeCashFlow",
                role="result",
                period=period,
            ),
        ],
    )
    facts = [
        _fact(
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            1_000,
            period,
            "Operating Cash Flow",
        ),
        _fact("custom:CapitalExpenditure", 200, period, "CapEx"),
        _fact("custom:FreeCashFlow", 800, period, "Free Cash Flow"),
    ]

    result, trace = composer._run_calculations(plan, facts)

    assert result == pytest.approx(800.0)
    assert any("Operating cash flow (2023-12-31): 1,000.00" in line for line in trace)
    assert any("Capital expenditure (2023-12-31): 200.00" in line for line in trace)
    assert any("Free cash flow (2023-12-31): 1,000.00 - 200.00 = 800.00" in line for line in trace)
    assert any("Grounded FCF fact (2023-12-31): 800.00" in line for line in trace)


def test_compose_text_uses_chinese_intro_and_result_label() -> None:
    composer = _composer()
    plan = QueryPlan(
        original_query="特斯拉 FY2023 的营收是多少？",
        normalized_query="revenue FY2023",
        query_language=QueryLanguage.CHINESE,
        query_type=QueryType.NUMERIC_CALCULATION,
        required_periods=[date(2023, 12, 31)],
        required_concepts=["us-gaap:Revenues"],
        needs_calculation=True,
    )
    text = composer._compose_text(
        plan,
        EvidenceBundle(
            plan_id=plan.plan_id,
            facts=[_fact("us-gaap:Revenues", 96773.0, date(2023, 12, 31), "Revenue")],
        ),
        96773.0,
        ["Revenue: 96,773.00 USD (period ending 2023-12-31)"],
    )
    assert text.startswith("根据 Tesla SEC 财报：")
    assert "\n结果: 96,773.00" in text


def test_compose_limitation_text_uses_chinese_header() -> None:
    composer = _composer()
    plan = QueryPlan(
        original_query="截至2023年12月31日，特斯拉的现金及现金等价物是多少？",
        normalized_query="cash FY2023",
        query_language=QueryLanguage.CHINESE,
    )
    text = composer._compose_limitation_text(plan, ["Missing grounded evidence"])
    assert text.startswith("无法基于现有证据为这个问题提供完全有依据的答案。")


def test_display_ratio_as_percent_uses_normalized_query() -> None:
    plan = QueryPlan(
        original_query="特斯拉 FY2023 的毛利率是多少？",
        normalized_query="gross margin FY2023",
        query_language=QueryLanguage.CHINESE,
    )
    assert GroundedAnswerComposer._display_ratio_as_percent(plan) is True
