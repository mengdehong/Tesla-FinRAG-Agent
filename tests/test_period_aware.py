"""Tests for period-aware multi-step financial QA features.

Covers:
- Period semantics classification (planner and calculator levels)
- Sub-query decomposition for multi-period questions
- Period compatibility validation
- Q4 standalone derivation (FY - Q1 - Q2 - Q3)
- Incompatible period rejection in calculations
- Cross-year comparison pipeline flow
- Multi-quarter ranking pipeline flow
- Limitation text composition
- Evidence sufficiency guardrails in the answer composer
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.calculation.calculator import (
    PeriodIncompatibleError,
    StructuredCalculator,
    are_periods_compatible,
    classify_fact_period,
    derive_standalone_quarter,
)
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AnswerStatus,
    EvidenceBundle,
    FactRecord,
    PeriodSemantics,
    QueryPlan,
    QueryType,
)
from tesla_finrag.planning.query_planner import (
    RuleBasedQueryPlanner,
    _build_sub_queries,
    _needs_decomposition,
    build_period_semantics_map,
    classify_period_semantics,
)
from tesla_finrag.retrieval.in_memory import (
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fact(
    concept: str,
    value: float,
    period_end: date,
    *,
    period_start: date | None = None,
    is_instant: bool = False,
    doc_id=None,
) -> FactRecord:
    """Build a minimal FactRecord for testing."""
    return FactRecord(
        doc_id=doc_id or uuid4(),
        concept=concept,
        label=concept.split(":")[-1],
        value=value,
        unit="USD",
        scale=1_000_000,
        period_start=period_start,
        period_end=period_end,
        is_instant=is_instant,
    )


# ===================================================================
# 1. Period semantics classification
# ===================================================================


class TestClassifyPeriodSemantics:
    """Tests for classify_period_semantics (planner-level)."""

    def test_annual_cumulative_from_fy_keyword(self):
        sem = classify_period_semantics(date(2022, 12, 31), "What was revenue in FY2022?")
        assert sem == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_annual_cumulative_from_standalone_year(self):
        sem = classify_period_semantics(date(2023, 12, 31), "Revenue in 2023")
        assert sem == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_quarterly_standalone_from_q_label(self):
        sem = classify_period_semantics(date(2022, 3, 31), "Revenue in Q1 2022")
        assert sem == PeriodSemantics.QUARTERLY_STANDALONE

    def test_quarterly_standalone_q2(self):
        sem = classify_period_semantics(date(2023, 6, 30), "Revenue in Q2 2023")
        assert sem == PeriodSemantics.QUARTERLY_STANDALONE

    def test_quarterly_standalone_q3(self):
        sem = classify_period_semantics(date(2022, 9, 30), "Revenue in 2022 Q3")
        assert sem == PeriodSemantics.QUARTERLY_STANDALONE

    def test_q4_explicit_is_quarterly_standalone(self):
        """An explicit Q4 mention should classify as QUARTERLY_STANDALONE, not annual."""
        sem = classify_period_semantics(date(2022, 12, 31), "Revenue in Q4 2022")
        assert sem == PeriodSemantics.QUARTERLY_STANDALONE

    def test_unknown_for_nonstandard_date(self):
        sem = classify_period_semantics(date(2022, 5, 15), "Revenue in May 2022")
        assert sem == PeriodSemantics.UNKNOWN


class TestClassifyFactPeriod:
    """Tests for classify_fact_period (calculator-level)."""

    def test_annual_cumulative_with_period_start(self):
        fact = _make_fact(
            "us-gaap:Revenues",
            81_462.0,
            date(2022, 12, 31),
            period_start=date(2022, 1, 1),
        )
        assert classify_fact_period(fact) == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_annual_cumulative_without_period_start(self):
        """A 12/31 fact without explicit period_start is still assumed annual."""
        fact = _make_fact("us-gaap:Revenues", 81_462.0, date(2022, 12, 31))
        assert classify_fact_period(fact) == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_quarterly_standalone(self):
        fact = _make_fact("us-gaap:Revenues", 18_756.0, date(2022, 3, 31))
        assert classify_fact_period(fact) == PeriodSemantics.QUARTERLY_STANDALONE

    def test_quarterly_q2(self):
        fact = _make_fact("us-gaap:Revenues", 16_934.0, date(2022, 6, 30))
        assert classify_fact_period(fact) == PeriodSemantics.QUARTERLY_STANDALONE

    def test_quarterly_q3(self):
        fact = _make_fact("us-gaap:Revenues", 21_454.0, date(2022, 9, 30))
        assert classify_fact_period(fact) == PeriodSemantics.QUARTERLY_STANDALONE

    def test_instant_fact(self):
        fact = _make_fact(
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            16_000.0,
            date(2022, 12, 31),
            is_instant=True,
        )
        assert classify_fact_period(fact) == PeriodSemantics.INSTANT


class TestBuildPeriodSemanticsMap:
    """Tests for build_period_semantics_map."""

    def test_multi_period_map(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        sem_map = build_period_semantics_map(periods, "Compare FY2022 vs FY2023 revenue")
        assert sem_map["2022-12-31"] == PeriodSemantics.ANNUAL_CUMULATIVE
        assert sem_map["2023-12-31"] == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_mixed_quarter_and_annual(self):
        periods = [date(2022, 3, 31), date(2022, 12, 31)]
        sem_map = build_period_semantics_map(periods, "Compare Q1 2022 revenue to FY2022 revenue")
        assert sem_map["2022-03-31"] == PeriodSemantics.QUARTERLY_STANDALONE
        assert sem_map["2022-12-31"] == PeriodSemantics.ANNUAL_CUMULATIVE


# ===================================================================
# 2. Sub-query decomposition
# ===================================================================


class TestNeedsDecomposition:
    """Tests for _needs_decomposition."""

    def test_single_period_no_decomposition(self):
        assert _needs_decomposition("Revenue in FY2022", [date(2022, 12, 31)]) is False

    def test_no_periods_no_decomposition(self):
        assert _needs_decomposition("What is Tesla's revenue?", []) is False

    def test_comparison_triggers_decomposition(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        assert _needs_decomposition("Compare revenue between FY2022 and FY2023", periods) is True

    def test_ranking_triggers_decomposition(self):
        periods = [date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30)]
        assert (
            _needs_decomposition("Which quarter had the highest revenue in 2022?", periods) is True
        )

    def test_change_triggers_decomposition(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        assert _needs_decomposition("Revenue change from 2022 to 2023", periods) is True

    def test_multiple_periods_without_comparison_keywords_still_decompose(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        assert _needs_decomposition("Revenue in FY2022 and FY2023", periods) is True


class TestBuildSubQueries:
    """Tests for _build_sub_queries."""

    def test_sub_queries_for_two_periods(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        sem_map = {
            "2022-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
            "2023-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
        }
        concepts = ["us-gaap:Revenues"]

        sqs = _build_sub_queries("Compare revenue", periods, concepts, sem_map)
        assert len(sqs) == 2
        assert sqs[0].target_period == date(2022, 12, 31)
        assert sqs[0].period_semantics == PeriodSemantics.ANNUAL_CUMULATIVE
        assert sqs[0].target_concepts == ["us-gaap:Revenues"]
        assert "Revenues" in sqs[0].text
        assert "FY2022" in sqs[0].text
        assert sqs[1].target_period == date(2023, 12, 31)
        assert "FY2023" in sqs[1].text

    def test_sub_queries_for_quarterly(self):
        periods = [date(2022, 3, 31), date(2022, 6, 30)]
        sem_map = {
            "2022-03-31": PeriodSemantics.QUARTERLY_STANDALONE,
            "2022-06-30": PeriodSemantics.QUARTERLY_STANDALONE,
        }
        concepts = ["us-gaap:GrossProfit"]

        sqs = _build_sub_queries("Compare gross profit", periods, concepts, sem_map)
        assert len(sqs) == 2
        assert "Q1 2022" in sqs[0].text
        assert "Q2 2022" in sqs[1].text

    def test_sub_queries_without_concepts_use_question_context(self):
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        sem_map = {
            "2022-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
            "2023-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
        }
        sqs = _build_sub_queries("Discuss supply chain issues", periods, [], sem_map)
        assert len(sqs) == 2
        assert sqs[0].target_concepts == []
        assert "Discuss supply chain issues" in sqs[0].text
        assert "FY2022" in sqs[0].text
        assert "FY2023" in sqs[1].text

    def test_no_sub_queries_without_periods(self):
        sem_map = {}
        sqs = _build_sub_queries("something", [], ["us-gaap:Revenues"], sem_map)
        assert sqs == []


# ===================================================================
# 3. Period compatibility validation
# ===================================================================


class TestArePeriodsCompatible:
    """Tests for are_periods_compatible."""

    def test_same_semantics_compatible(self):
        assert are_periods_compatible(
            PeriodSemantics.ANNUAL_CUMULATIVE,
            PeriodSemantics.ANNUAL_CUMULATIVE,
        )

    def test_quarterly_compatible(self):
        assert are_periods_compatible(
            PeriodSemantics.QUARTERLY_STANDALONE,
            PeriodSemantics.QUARTERLY_STANDALONE,
        )

    def test_annual_vs_quarterly_incompatible(self):
        assert not are_periods_compatible(
            PeriodSemantics.ANNUAL_CUMULATIVE,
            PeriodSemantics.QUARTERLY_STANDALONE,
        )

    def test_unknown_is_incompatible_with_known_semantics(self):
        assert not are_periods_compatible(
            PeriodSemantics.UNKNOWN,
            PeriodSemantics.ANNUAL_CUMULATIVE,
        )
        assert not are_periods_compatible(
            PeriodSemantics.QUARTERLY_STANDALONE,
            PeriodSemantics.UNKNOWN,
        )

    def test_unknown_is_incompatible_with_unknown(self):
        assert not are_periods_compatible(PeriodSemantics.UNKNOWN, PeriodSemantics.UNKNOWN)

    def test_instant_vs_annual_incompatible(self):
        assert not are_periods_compatible(
            PeriodSemantics.INSTANT,
            PeriodSemantics.ANNUAL_CUMULATIVE,
        )


# ===================================================================
# 4. Q4 standalone derivation
# ===================================================================


class TestDeriveStandaloneQuarter:
    """Tests for derive_standalone_quarter (Q4 = FY - Q1 - Q2 - Q3)."""

    def test_derive_q4_revenue(self):
        """Derive Q4 revenue from FY and Q1-Q3."""
        facts = [
            _make_fact("us-gaap:Revenues", 81_462.0, date(2022, 12, 31)),  # FY
            _make_fact("us-gaap:Revenues", 18_756.0, date(2022, 3, 31)),  # Q1
            _make_fact("us-gaap:Revenues", 16_934.0, date(2022, 6, 30)),  # Q2
            _make_fact("us-gaap:Revenues", 21_454.0, date(2022, 9, 30)),  # Q3
        ]
        derived, trace = derive_standalone_quarter("us-gaap:Revenues", 2022, 4, facts)
        assert derived is not None

        # Q4 = 81,462 - 18,756 - 16,934 - 21,454 = 24,318 (all in millions)
        expected = (81_462.0 - 18_756.0 - 16_934.0 - 21_454.0) * 1_000_000
        assert derived == pytest.approx(expected)
        assert any("Q4 = FY - Q1 - Q2 - Q3" in line for line in trace)

    def test_derive_q4_missing_q2(self):
        """Cannot derive Q4 if Q2 is missing."""
        facts = [
            _make_fact("us-gaap:Revenues", 81_462.0, date(2022, 12, 31)),
            _make_fact("us-gaap:Revenues", 18_756.0, date(2022, 3, 31)),
            # Q2 missing
            _make_fact("us-gaap:Revenues", 21_454.0, date(2022, 9, 30)),
        ]
        derived, trace = derive_standalone_quarter("us-gaap:Revenues", 2022, 4, facts)
        assert derived is None
        assert any("Missing Q2" in line for line in trace)

    def test_derive_q4_missing_fy(self):
        """Cannot derive Q4 if FY is missing."""
        facts = [
            _make_fact("us-gaap:Revenues", 18_756.0, date(2022, 3, 31)),
            _make_fact("us-gaap:Revenues", 16_934.0, date(2022, 6, 30)),
            _make_fact("us-gaap:Revenues", 21_454.0, date(2022, 9, 30)),
        ]
        derived, trace = derive_standalone_quarter("us-gaap:Revenues", 2022, 4, facts)
        assert derived is None
        assert any("No FY2022" in line for line in trace)

    def test_derive_non_q4_unsupported(self):
        """Derivation for quarters other than Q4 is not supported yet."""
        derived, trace = derive_standalone_quarter("us-gaap:Revenues", 2022, 2, [])
        assert derived is None
        assert any("only supports Q4" in line for line in trace)


# ===================================================================
# 5. Incompatible period rejection in calculations
# ===================================================================


class TestIncompatiblePeriodRejection:
    """Tests that period_over_period rejects incompatible periods."""

    def test_annual_vs_quarterly_raises(self):
        """Comparing an annual fact to a quarterly fact should raise."""
        facts = [
            _make_fact(
                "us-gaap:Revenues",
                81_462.0,
                date(2022, 12, 31),
                period_start=date(2022, 1, 1),
            ),
            _make_fact("us-gaap:Revenues", 23_329.0, date(2023, 3, 31)),
        ]
        calc = StructuredCalculator()
        with pytest.raises(PeriodIncompatibleError) as exc_info:
            calc.period_over_period(
                facts,
                "us-gaap:Revenues",
                date(2022, 12, 31),
                date(2023, 3, 31),
            )
        assert "annual_cumulative" in str(exc_info.value)
        assert "quarterly_standalone" in str(exc_info.value)
        assert exc_info.value.details["concept"] == "us-gaap:Revenues"

    def test_quarterly_vs_quarterly_passes(self):
        """Comparing two quarterly facts should work fine."""
        facts = [
            _make_fact("us-gaap:Revenues", 18_756.0, date(2022, 3, 31)),
            _make_fact("us-gaap:Revenues", 16_934.0, date(2022, 6, 30)),
        ]
        calc = StructuredCalculator()
        change, trace = calc.period_over_period(
            facts,
            "us-gaap:Revenues",
            date(2022, 3, 31),
            date(2022, 6, 30),
        )
        expected = (16_934.0 - 18_756.0) * 1_000_000
        assert change == pytest.approx(expected)

    def test_annual_vs_annual_passes(self):
        """Comparing two annual facts should work fine."""
        facts = [
            _make_fact(
                "us-gaap:Revenues",
                81_462.0,
                date(2022, 12, 31),
                period_start=date(2022, 1, 1),
            ),
            _make_fact(
                "us-gaap:Revenues",
                96_773.0,
                date(2023, 12, 31),
                period_start=date(2023, 1, 1),
            ),
        ]
        calc = StructuredCalculator()
        change, trace = calc.period_over_period(
            facts,
            "us-gaap:Revenues",
            date(2022, 12, 31),
            date(2023, 12, 31),
        )
        expected = (96_773.0 - 81_462.0) * 1_000_000
        assert change == pytest.approx(expected)

    def test_validate_semantics_off_skips_check(self):
        """validate_semantics=False should bypass the compatibility check."""
        facts = [
            _make_fact(
                "us-gaap:Revenues",
                81_462.0,
                date(2022, 12, 31),
                period_start=date(2022, 1, 1),
            ),
            _make_fact("us-gaap:Revenues", 23_329.0, date(2023, 3, 31)),
        ]
        calc = StructuredCalculator()
        # Should not raise
        change, trace = calc.period_over_period(
            facts,
            "us-gaap:Revenues",
            date(2022, 12, 31),
            date(2023, 3, 31),
            validate_semantics=False,
        )
        expected = (23_329.0 - 81_462.0) * 1_000_000
        assert change == pytest.approx(expected)

    def test_period_incompatible_error_has_details(self):
        """PeriodIncompatibleError should carry structured details."""
        err = PeriodIncompatibleError(
            "test message",
            details={"semantics_a": "annual_cumulative", "semantics_b": "quarterly_standalone"},
        )
        assert str(err) == "test message"
        assert err.details["semantics_a"] == "annual_cumulative"

    def test_unknown_period_semantics_raise(self):
        """Unknown temporal semantics should be rejected for arithmetic."""
        facts = [
            _make_fact("us-gaap:Revenues", 12_000.0, date(2022, 11, 30)),
            _make_fact(
                "us-gaap:Revenues",
                81_462.0,
                date(2022, 12, 31),
                period_start=date(2022, 1, 1),
            ),
        ]
        calc = StructuredCalculator()
        with pytest.raises(PeriodIncompatibleError) as exc_info:
            calc.period_over_period(
                facts,
                "us-gaap:Revenues",
                date(2022, 11, 30),
                date(2022, 12, 31),
            )
        assert "unknown" in str(exc_info.value)
        assert exc_info.value.details["semantics_a"] == "unknown"


# ===================================================================
# 6. Planner integration: cross-year comparison
# ===================================================================


class TestPlannerCrossYear:
    """Full planner tests for cross-year comparison questions."""

    def test_cross_year_revenue_comparison(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("Compare Tesla's revenue in FY2022 vs FY2023")

        assert len(plan.required_periods) == 2
        assert date(2022, 12, 31) in plan.required_periods
        assert date(2023, 12, 31) in plan.required_periods
        assert "us-gaap:Revenues" in plan.required_concepts
        assert plan.needs_calculation is True

        # Should have sub-queries for each period
        assert len(plan.sub_queries) == 2
        assert plan.sub_queries[0].target_period == date(2022, 12, 31)
        assert plan.sub_queries[1].target_period == date(2023, 12, 31)

        # Period semantics should be populated
        assert plan.period_semantics["2022-12-31"] == PeriodSemantics.ANNUAL_CUMULATIVE
        assert plan.period_semantics["2023-12-31"] == PeriodSemantics.ANNUAL_CUMULATIVE

    def test_cross_year_growth_question(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("How much did Tesla's gross profit grow from 2022 to 2023?")

        assert len(plan.required_periods) == 2
        assert "us-gaap:GrossProfit" in plan.required_concepts
        assert plan.needs_calculation is True
        assert len(plan.sub_queries) >= 2

    def test_single_period_no_sub_queries(self):
        """A single-period question should not produce sub-queries."""
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What was Tesla's revenue in FY2022?")

        assert len(plan.required_periods) == 1
        assert plan.sub_queries == []

    def test_multi_period_lookup_without_comparison_keywords_still_decomposes(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What was Tesla's revenue in FY2022 and FY2023?")

        assert len(plan.required_periods) == 2
        assert len(plan.sub_queries) == 2
        assert plan.sub_queries[0].target_period == date(2022, 12, 31)
        assert plan.sub_queries[1].target_period == date(2023, 12, 31)

    def test_multi_period_narrative_without_metrics_still_decomposes(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("Discuss Tesla's supply chain challenges in FY2022 and FY2023")

        assert plan.query_type == QueryType.NARRATIVE_COMPARE
        assert len(plan.sub_queries) == 2
        assert all(sub_query.target_concepts == [] for sub_query in plan.sub_queries)


# ===================================================================
# 7. Planner integration: multi-quarter ranking
# ===================================================================


class TestPlannerMultiQuarterRanking:
    """Full planner tests for multi-quarter ranking questions."""

    def test_highest_quarter_ranking(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("Which quarter had the highest revenue: Q1 2022, Q2 2022, or Q3 2022?")

        assert len(plan.required_periods) == 3
        assert date(2022, 3, 31) in plan.required_periods
        assert date(2022, 6, 30) in plan.required_periods
        assert date(2022, 9, 30) in plan.required_periods
        assert "us-gaap:Revenues" in plan.required_concepts
        assert plan.needs_calculation is True
        assert len(plan.sub_queries) == 3

    def test_lowest_quarter_question(self):
        planner = RuleBasedQueryPlanner()
        plan = planner.plan("Which quarter had the lowest operating income in 2023: Q1, Q2, or Q3?")
        assert "us-gaap:OperatingIncomeLoss" in plan.required_concepts
        assert plan.needs_calculation is True


# ===================================================================
# 8. Limitation text composition
# ===================================================================


class TestComposeLimitationText:
    """Tests for GroundedAnswerComposer._compose_limitation_text."""

    def test_single_reason(self):
        text = GroundedAnswerComposer._compose_limitation_text(
            ["Missing grounded facts for period(s): 2024-12-31"]
        )
        assert "Unable to provide a fully grounded answer" in text
        assert "Missing grounded facts" in text

    def test_multiple_reasons(self):
        reasons = [
            "Missing grounded facts for period(s): 2024-12-31",
            "Semantics: annual_cumulative vs quarterly_standalone",
        ]
        text = GroundedAnswerComposer._compose_limitation_text(reasons)
        assert "Unable to provide a fully grounded answer" in text
        # Multi-reason format uses bullet points
        assert "- Missing grounded facts" in text
        assert "- Semantics:" in text


# ===================================================================
# 9. Evidence sufficiency guardrails
# ===================================================================


class TestEvidenceSufficiencyGuardrails:
    """Tests for evidence sufficiency guardrails in the composer.

    Uses the shared fixtures from conftest.py.
    """

    def test_missing_periods_returns_limitation(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """When required periods have no matching facts, return limitation status."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        calculator = StructuredCalculator()
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=calculator,
            linker=linker,
        )

        # Create a plan requiring a period that has no facts (2024)
        plan = QueryPlan(
            original_query="What was Tesla's revenue in FY2024?",
            query_type=QueryType.NUMERIC_CALCULATION,
            sub_questions=["What was Tesla's revenue in FY2024?"],
            required_periods=[date(2024, 12, 31)],
            period_semantics={"2024-12-31": PeriodSemantics.ANNUAL_CUMULATIVE},
            required_concepts=["us-gaap:Revenues"],
            needs_calculation=True,
        )
        bundle = EvidenceBundle(plan_id=plan.plan_id)

        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer.confidence == 0.0
        assert "Unable to provide a fully grounded answer" in answer.answer_text
        assert answer.retrieval_debug["missing_periods"]

    def test_period_incompatible_error_returns_calculation_error(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """When calculator raises PeriodIncompatibleError, return calculation_error status."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        calculator = StructuredCalculator()
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=calculator,
            linker=linker,
        )

        # Plan comparing annual (FY2022) with quarterly (Q1 2023)
        plan = QueryPlan(
            original_query="Compare revenue FY2022 vs Q1 2023",
            query_type=QueryType.NUMERIC_CALCULATION,
            sub_questions=["Compare revenue FY2022 vs Q1 2023"],
            required_periods=[date(2022, 12, 31), date(2023, 3, 31)],
            period_semantics={
                "2022-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
                "2023-03-31": PeriodSemantics.QUARTERLY_STANDALONE,
            },
            required_concepts=["us-gaap:Revenues"],
            needs_calculation=True,
        )
        bundle = EvidenceBundle(plan_id=plan.plan_id)

        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.CALCULATION_ERROR
        assert answer.confidence == 0.0
        assert "Unable to provide a fully grounded answer" in answer.answer_text
        assert answer.retrieval_debug.get("limitation_reasons")

    def test_compatible_periods_returns_ok(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """When facts exist for all required periods and are compatible, return OK."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        calculator = StructuredCalculator()
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=calculator,
            linker=linker,
        )

        # Plan comparing Q1 2022 vs Q1 2023 (both quarterly, compatible)
        plan = QueryPlan(
            original_query="Compare revenue Q1 2022 vs Q1 2023",
            query_type=QueryType.NUMERIC_CALCULATION,
            sub_questions=["Compare revenue Q1 2022 vs Q1 2023"],
            required_periods=[date(2022, 3, 31), date(2023, 3, 31)],
            period_semantics={
                "2022-03-31": PeriodSemantics.QUARTERLY_STANDALONE,
                "2023-03-31": PeriodSemantics.QUARTERLY_STANDALONE,
            },
            required_concepts=["us-gaap:Revenues"],
            needs_calculation=True,
        )
        bundle = EvidenceBundle(plan_id=plan.plan_id)

        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert answer.confidence > 0.0
        assert "limitation" not in answer.answer_text.lower()

    def test_missing_required_concept_returns_limitation(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """A period with partial concepts should still fail guardrails."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        calculator = StructuredCalculator()
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=calculator,
            linker=linker,
        )

        # FY2023 has FreeCashFlow in fixtures, but no CapitalExpenditure fact.
        plan = QueryPlan(
            original_query="Compute free cash flow from operating cash flow and capex in FY2023",
            query_type=QueryType.NUMERIC_CALCULATION,
            sub_questions=["Compute free cash flow from operating cash flow and capex in FY2023"],
            required_periods=[date(2023, 12, 31)],
            period_semantics={"2023-12-31": PeriodSemantics.ANNUAL_CUMULATIVE},
            required_concepts=["custom:FreeCashFlow", "custom:CapitalExpenditure"],
            needs_calculation=True,
        )
        bundle = EvidenceBundle(plan_id=plan.plan_id)

        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert "2023-12-31" in answer.retrieval_debug["missing_periods"]
        assert (
            "custom:CapitalExpenditure"
            in answer.retrieval_debug["missing_concepts_by_period"]["2023-12-31"]
        )

    def test_non_calculation_missing_period_returns_limitation(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """Required periods must still fail closed for non-calculation plans."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=StructuredCalculator(),
            linker=linker,
        )

        plan = QueryPlan(
            original_query="Discuss Tesla's supply chain issues in FY2022 and FY2025",
            query_type=QueryType.NARRATIVE_COMPARE,
            sub_questions=["Discuss Tesla's supply chain issues in FY2022 and FY2025"],
            required_periods=[date(2022, 12, 31), date(2025, 12, 31)],
            period_semantics={
                "2022-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
                "2025-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
            },
            needs_calculation=False,
        )

        answer = composer.answer(plan, EvidenceBundle(plan_id=plan.plan_id))

        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert "2025-12-31" in answer.retrieval_debug["missing_periods"]
        assert "Missing grounded evidence" in answer.answer_text

    def test_non_calculation_missing_narrative_evidence_returns_limitation(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """Facts alone should not satisfy a narrative question for required periods."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=StructuredCalculator(),
            linker=linker,
        )

        plan = QueryPlan(
            original_query="Discuss Tesla's supply chain issues in FY2022 and FY2023",
            query_type=QueryType.NARRATIVE_COMPARE,
            sub_questions=["Discuss Tesla's supply chain issues in FY2022 and FY2023"],
            required_periods=[date(2022, 12, 31), date(2023, 12, 31)],
            period_semantics={
                "2022-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
                "2023-12-31": PeriodSemantics.ANNUAL_CUMULATIVE,
            },
            needs_calculation=False,
        )

        answer = composer.answer(plan, EvidenceBundle(plan_id=plan.plan_id))

        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer.retrieval_debug["missing_periods"] == []
        assert answer.retrieval_debug["missing_narrative_periods"] == [
            "2022-12-31",
            "2023-12-31",
        ]
        assert "Missing supporting narrative evidence" in answer.answer_text


# ===================================================================
# 10. Full pipeline integration: cross-year comparison
# ===================================================================


class TestCrossYearComparisonPipeline:
    """End-to-end test: plan → retrieve → link → compose for cross-year."""

    def test_revenue_fy2022_vs_fy2023(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service,
        linker: EvidenceLinker,
        composer: GroundedAnswerComposer,
    ):
        plan = planner.plan("Compare Tesla's revenue in FY2022 vs FY2023")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert answer.confidence > 0.0
        # The calculation trace should contain both periods
        trace_text = " ".join(answer.calculation_trace)
        assert "2022-12-31" in trace_text
        assert "2023-12-31" in trace_text

    def test_missing_period_sub_query_does_not_fallback_unscoped(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service,
        composer: GroundedAnswerComposer,
    ):
        """Per-period retrieval must fail closed when one target period is absent."""
        plan = planner.plan("Compare Tesla's revenue between FY2022 and FY2025")
        assert len(plan.sub_queries) == 2

        bundle = retrieval_service.retrieve(plan)
        period_meta = bundle.metadata.get("per_period", {})

        assert bundle.metadata["retrieval_mode"] == "per_period"
        assert period_meta["2025-12-31"]["scope_miss"] is True
        assert period_meta["2025-12-31"]["doc_id_filter"] == []
        assert period_meta["2025-12-31"]["lexical_hits"] == 0
        assert period_meta["2025-12-31"]["vector_hits"] == 0

        answer = composer.answer(plan, bundle)
        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert "2025-12-31" in answer.retrieval_debug["missing_periods"]


class TestQ4DerivedPipeline:
    """End-to-end test for standalone Q4 derivation."""

    def test_q4_standalone_revenue_is_derived(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service,
        composer: GroundedAnswerComposer,
    ):
        plan = planner.plan("What was Tesla's revenue in Q4 2023?")
        assert plan.period_semantics["2023-12-31"] == PeriodSemantics.QUARTERLY_STANDALONE

        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        trace_text = "\n".join(answer.calculation_trace)
        assert "Deriving Q4 2023 for us-gaap:Revenues" in trace_text
        assert "Q4 = FY - Q1 - Q2 - Q3" in trace_text
        assert "25,167,000,000" in trace_text

    def test_q4_instant_metric_uses_existing_instant_fact(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """Instant metrics at 12/31 should not trigger standalone-Q4 derivation."""
        filing = next(f for f in corpus_repo.list_filings() if f.period_end == date(2023, 12, 31))
        facts_repo.upsert_fact(
            FactRecord(
                doc_id=filing.doc_id,
                concept="us-gaap:CashAndCashEquivalentsAtCarryingValue",
                label="Cash and Cash Equivalents",
                value=29_094.0,
                unit="USD",
                scale=1_000_000,
                period_end=date(2023, 12, 31),
                is_instant=True,
            )
        )

        composer = GroundedAnswerComposer(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            calculator=StructuredCalculator(),
            linker=EvidenceLinker(corpus_repo, facts_repo),
        )
        plan = QueryPlan(
            original_query="What was Tesla's cash and cash equivalents in Q4 2023?",
            query_type=QueryType.NUMERIC_CALCULATION,
            sub_questions=["What was Tesla's cash and cash equivalents in Q4 2023?"],
            required_periods=[date(2023, 12, 31)],
            period_semantics={"2023-12-31": PeriodSemantics.QUARTERLY_STANDALONE},
            required_concepts=["us-gaap:CashAndCashEquivalentsAtCarryingValue"],
            needs_calculation=True,
        )

        answer = composer.answer(plan, EvidenceBundle(plan_id=plan.plan_id))

        assert answer.status == AnswerStatus.OK
        trace_text = "\n".join(answer.calculation_trace)
        assert "Cash and Cash Equivalents" in trace_text
        assert "Deriving Q4 2023" not in trace_text
        assert "Unable to derive standalone Q4" not in answer.answer_text


class TestMultiQuarterRankingPipeline:
    """End-to-end test: plan → retrieve → link → compose for ranking."""

    def test_rank_quarterly_revenue_2022(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service,
        linker: EvidenceLinker,
        composer: GroundedAnswerComposer,
    ):
        plan = planner.plan(
            "Which quarter had the highest revenue in 2022: Q1 2022, Q2 2022, or Q3 2022?"
        )
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        # The trace should rank values
        trace_text = " ".join(answer.calculation_trace)
        # Q3 2022 had the highest revenue (21,454)
        assert "21,454" in trace_text.replace(",", ",") or "2022-09-30" in trace_text

    def test_multi_period_lookup_without_comparison_keywords_uses_per_period_retrieval(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service,
        composer: GroundedAnswerComposer,
    ):
        """Explicit multi-period lookups still require per-period retrieval."""
        plan = planner.plan("What was Tesla's revenue in FY2022 and FY2025?")
        assert len(plan.sub_queries) == 2

        bundle = retrieval_service.retrieve(plan)

        assert bundle.metadata["retrieval_mode"] == "per_period"
        assert bundle.metadata["per_period"]["2025-12-31"]["scope_miss"] is True

        answer = composer.answer(plan, bundle)
        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert "2025-12-31" in answer.retrieval_debug["missing_periods"]


# ===================================================================
# 11. Evidence linker period coverage
# ===================================================================


class TestEvidenceLinkerPeriodCoverage:
    """Tests for EvidenceLinker period coverage metadata."""

    def test_missing_periods_detected(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """Linker should detect when required periods lack fact coverage."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        plan_id = uuid4()
        bundle = EvidenceBundle(plan_id=plan_id)

        enriched = linker.link(
            bundle,
            required_concepts=["us-gaap:Revenues"],
            required_periods=[date(2022, 12, 31), date(2024, 12, 31)],
        )

        missing = enriched.metadata.get("missing_periods", [])
        assert "2024-12-31" in missing
        # 2022-12-31 should have data
        assert "2022-12-31" not in missing

    def test_all_periods_covered(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """Linker should report no missing periods when all are covered."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        plan_id = uuid4()
        bundle = EvidenceBundle(plan_id=plan_id)

        enriched = linker.link(
            bundle,
            required_concepts=["us-gaap:Revenues"],
            required_periods=[date(2022, 12, 31), date(2023, 12, 31)],
        )

        missing = enriched.metadata.get("missing_periods", [])
        assert missing == []

        coverage = enriched.metadata.get("period_coverage", {})
        assert coverage["2022-12-31"]["has_facts"] is True
        assert coverage["2023-12-31"]["has_facts"] is True

    def test_missing_required_concepts_marks_period_missing(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ):
        """A period is missing if required concepts are incomplete."""
        linker = EvidenceLinker(corpus_repo, facts_repo)
        plan_id = uuid4()
        bundle = EvidenceBundle(plan_id=plan_id)

        enriched = linker.link(
            bundle,
            required_concepts=["custom:FreeCashFlow", "custom:CapitalExpenditure"],
            required_periods=[date(2023, 12, 31)],
        )

        assert enriched.metadata["missing_periods"] == ["2023-12-31"]
        assert enriched.metadata["missing_concepts_by_period"]["2023-12-31"] == [
            "custom:CapitalExpenditure"
        ]
        coverage = enriched.metadata["period_coverage"]["2023-12-31"]
        assert coverage["has_facts"] is True
        assert coverage["has_required_concepts"] is False
