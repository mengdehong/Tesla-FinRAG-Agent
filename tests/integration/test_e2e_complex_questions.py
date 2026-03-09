"""End-to-end tests for complex Tesla financial questions.

Covers task 4.2: verifies that representative complex Tesla questions
return traceable answers without delegating arithmetic to free-form
generation.  Each test runs the full pipeline: plan -> retrieve ->
answer, then asserts on traceability properties.
"""

from __future__ import annotations

from datetime import date

import pytest

from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.models import AnswerStatus, QueryType
from tesla_finrag.planning.query_planner import RuleBasedQueryPlanner
from tesla_finrag.retrieval.hybrid import HybridRetrievalService
from tesla_finrag.retrieval.in_memory import (
    InMemoryCorpusRepository,
)

pytestmark = pytest.mark.integration


def _run_full_pipeline(
    question: str,
    planner: RuleBasedQueryPlanner,
    retrieval_service: HybridRetrievalService,
    composer: GroundedAnswerComposer,
):
    """Helper: run the complete plan -> retrieve -> answer pipeline."""
    plan = planner.plan(question)
    bundle = retrieval_service.retrieve(plan)
    answer = composer.answer(plan, bundle)
    return plan, bundle, answer


class TestRevenueComparisonE2E:
    """Revenue comparison questions: Q-over-Q and Y-over-Y."""

    def test_quarterly_revenue_comparison(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Compare Q1 2022 vs Q1 2023 revenue — arithmetic must be in trace."""
        plan, bundle, answer = _run_full_pipeline(
            "How did Tesla's revenue change from Q1 2022 to Q1 2023?",
            planner,
            retrieval_service,
            composer,
        )

        # 1) Plan must identify the two periods and revenue concept
        assert date(2022, 3, 31) in plan.required_periods
        assert date(2023, 3, 31) in plan.required_periods
        assert "us-gaap:Revenues" in plan.required_concepts
        assert plan.needs_calculation is True

        # 2) Answer must be OK with calculation trace
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

        # 3) The trace must contain the actual numeric values — no hallucinated math
        trace_text = "\n".join(answer.calculation_trace)
        assert "18,756" in trace_text or "18756" in trace_text  # Q1 2022 revenue
        assert "23,329" in trace_text or "23329" in trace_text  # Q1 2023 revenue

        # 4) Must have citations grounding the answer
        assert len(answer.citations) > 0

        # 5) Confidence should be reasonable
        assert answer.confidence is not None
        assert answer.confidence > 0.3

    def test_annual_revenue_comparison(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """FY2022 vs FY2023 revenue — ensure annual periods are detected."""
        plan, bundle, answer = _run_full_pipeline(
            "Compare Tesla's total revenue in FY2022 versus FY2023",
            planner,
            retrieval_service,
            composer,
        )
        assert date(2022, 12, 31) in plan.required_periods
        assert date(2023, 12, 31) in plan.required_periods
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

        trace_text = "\n".join(answer.calculation_trace)
        # FY2022: 81462, FY2023: 96773
        assert "81,462" in trace_text or "81462" in trace_text
        assert "96,773" in trace_text or "96773" in trace_text

    def test_chinese_annual_revenue_comparison(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Chinese equivalent query should produce the same grounded comparison."""
        plan, bundle, answer = _run_full_pipeline(
            "比较特斯拉FY2022和FY2023的总营收，同比增长率是多少？",
            planner,
            retrieval_service,
            composer,
        )
        assert date(2022, 12, 31) in plan.required_periods
        assert date(2023, 12, 31) in plan.required_periods
        assert answer.status == AnswerStatus.OK
        assert answer.answer_text.startswith("根据 Tesla SEC 财报：")
        trace_text = "\n".join(answer.calculation_trace)
        assert "81,462" in trace_text or "81462" in trace_text
        assert "96,773" in trace_text or "96773" in trace_text


class TestProfitabilityMetricsE2E:
    """Questions about margins, profits, and operating metrics."""

    def test_gross_profit_lookup(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Single-period gross profit lookup — value must come from facts."""
        plan, bundle, answer = _run_full_pipeline(
            "What was Tesla's gross profit in Q2 2022?",
            planner,
            retrieval_service,
            composer,
        )
        assert "us-gaap:GrossProfit" in plan.required_concepts
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

        trace_text = "\n".join(answer.calculation_trace)
        # Gross profit Q2 2022 = 4234 * 1_000_000
        assert "4,234" in trace_text or "4234" in trace_text

    def test_gross_margin_ratio(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Gross profit / Revenue ratio — must be computed, not generated."""
        plan, bundle, answer = _run_full_pipeline(
            "What was Tesla's gross profit to revenue ratio in Q1 2022?",
            planner,
            retrieval_service,
            composer,
        )
        assert answer.status == AnswerStatus.OK
        # Should have calculation trace with the ratio
        assert len(answer.calculation_trace) > 0

    def test_operating_income_trend(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Operating income change Q1 2022 -> Q1 2023 — arithmetic traceable."""
        plan, bundle, answer = _run_full_pipeline(
            "How did Tesla's operating income change from Q1 2022 to Q1 2023?",
            planner,
            retrieval_service,
            composer,
        )
        assert "us-gaap:OperatingIncomeLoss" in plan.required_concepts
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

        trace_text = "\n".join(answer.calculation_trace)
        # Q1 2022: 3600M, Q1 2023: 2664M
        assert "3,600" in trace_text or "3600" in trace_text
        assert "2,664" in trace_text or "2664" in trace_text


class TestFreeCashFlowE2E:
    """Free cash flow questions — custom concept handling."""

    def test_fcf_single_period(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Single-period FCF lookup must return the correct value."""
        plan, bundle, answer = _run_full_pipeline(
            "What was Tesla's free cash flow in Q3 2022?",
            planner,
            retrieval_service,
            composer,
        )
        assert "custom:FreeCashFlow" in plan.required_concepts
        assert answer.status == AnswerStatus.OK

        trace_text = "\n".join(answer.calculation_trace)
        # FCF Q3 2022 = 3297M
        assert "3,297" in trace_text or "3297" in trace_text

    def test_fcf_year_over_year(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """FCF comparison across years — change must appear in trace."""
        plan, bundle, answer = _run_full_pipeline(
            "How did Tesla's free cash flow change from FY2022 to FY2023?",
            planner,
            retrieval_service,
            composer,
        )
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

        trace_text = "\n".join(answer.calculation_trace)
        # FY2022 FCF: 7566, FY2023 FCF: 4358
        assert "7,566" in trace_text or "7566" in trace_text
        assert "4,358" in trace_text or "4358" in trace_text


class TestNarrativeE2E:
    """Pure narrative questions — no arithmetic should appear."""

    def test_supply_chain_narrative(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Supply chain question should return text evidence, no calculations."""
        plan, bundle, answer = _run_full_pipeline(
            "What supply chain challenges did Tesla discuss in their SEC filings?",
            planner,
            retrieval_service,
            composer,
        )
        assert plan.needs_calculation is False
        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) == 0
        # At least some citations should reference risk/MDA content
        assert len(answer.citations) > 0

    def test_pricing_strategy_narrative(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Pricing strategy question — evidence from MD&A sections."""
        plan, bundle, answer = _run_full_pipeline(
            "What did Tesla's management discuss about pricing strategy?",
            planner,
            retrieval_service,
            composer,
        )
        assert answer.status == AnswerStatus.OK
        assert answer.confidence is not None
        assert answer.confidence > 0.0


class TestTraceabilityGuarantees:
    """Verify structural guarantees: no arithmetic delegated to generation."""

    def test_numeric_answer_has_deterministic_trace(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Any numeric answer must have a non-empty calculation_trace."""
        questions = [
            "What was Tesla's total revenue in Q3 2022?",
            "How did revenue change from Q1 2022 to Q1 2023?",
            "What was Tesla's free cash flow in FY2023?",
        ]
        for question in questions:
            plan, bundle, answer = _run_full_pipeline(
                question,
                planner,
                retrieval_service,
                composer,
            )
            if plan.needs_calculation:
                assert len(answer.calculation_trace) > 0, (
                    f"Numeric question '{question}' produced no calculation trace"
                )

    def test_answer_text_contains_result_value(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """The answer text for numeric questions should include the computed result."""
        plan, bundle, answer = _run_full_pipeline(
            "What was Tesla's total revenue in Q3 2022?",
            planner,
            retrieval_service,
            composer,
        )
        # The answer text should mention "Result:" with the value
        if plan.query_type == QueryType.NUMERIC_CALCULATION:
            assert "Result:" in answer.answer_text or any(
                "21,454" in line for line in answer.calculation_trace
            )

    def test_all_citations_have_filing_metadata(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
        corpus_repo: InMemoryCorpusRepository,
    ) -> None:
        """Every citation must carry filing_type and period_end from an actual filing."""
        questions = [
            "What was Tesla's revenue in Q1 2023?",
            "What risk factors did Tesla mention?",
            "Show me the revenue breakdown by segment for Q3 2022",
        ]
        for question in questions:
            plan, bundle, answer = _run_full_pipeline(
                question,
                planner,
                retrieval_service,
                composer,
            )
            for citation in answer.citations:
                filing = corpus_repo.get_filing(citation.doc_id)
                assert filing is not None, f"Citation for '{question}' references unknown filing"
                assert citation.filing_type in ("10-K", "10-Q")
                assert citation.period_end is not None

    def test_calculation_never_delegated_to_generation(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Verify that the answer_text for numeric questions traces back to calc_trace.

        The answer text should include trace lines or a Result marker,
        confirming arithmetic came from the calculator, not free-form text.
        """
        plan, bundle, answer = _run_full_pipeline(
            "How did Tesla's revenue change from Q1 2022 to Q1 2023?",
            planner,
            retrieval_service,
            composer,
        )
        if plan.needs_calculation and answer.calculation_trace:
            # Every trace line should appear in the answer text
            for trace_line in answer.calculation_trace:
                assert trace_line in answer.answer_text, (
                    f"Trace line missing from answer text: {trace_line}"
                )

    def test_empty_bundle_produces_insufficient_evidence(
        self,
        composer: GroundedAnswerComposer,
    ) -> None:
        """An empty evidence bundle should never produce a confident answer."""
        from tesla_finrag.models import EvidenceBundle, QueryPlan

        plan = QueryPlan(
            original_query="What is the meaning of life?",
            sub_questions=["What is the meaning of life?"],
        )
        empty = EvidenceBundle(plan_id=plan.plan_id)
        answer = composer.answer(plan, empty)
        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer.confidence == 0.0
        assert len(answer.calculation_trace) == 0

    def test_idempotent_pipeline_results(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Running the same question twice should produce consistent calc results."""
        question = "What was Tesla's total revenue in Q3 2022?"

        plan1, _, answer1 = _run_full_pipeline(
            question,
            planner,
            retrieval_service,
            composer,
        )
        plan2, _, answer2 = _run_full_pipeline(
            question,
            planner,
            retrieval_service,
            composer,
        )

        # Calculation traces should be identical (deterministic)
        assert answer1.calculation_trace == answer2.calculation_trace
        assert answer1.status == answer2.status
        assert answer1.confidence == answer2.confidence
