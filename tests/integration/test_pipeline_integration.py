"""Integration tests for text-only, numeric, and text-plus-table financial questions.

Covers task 4.1: validates that the pipeline components work together
for each major question category.
"""

from __future__ import annotations

from datetime import date

import pytest

from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.calculation.calculator import CalcOp, StructuredCalculator
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AnswerStatus,
    EvidenceBundle,
    QueryType,
)
from tesla_finrag.planning.query_planner import (
    RuleBasedQueryPlanner,
    extract_metrics,
    extract_periods,
)
from tesla_finrag.retrieval.hybrid import HybridRetrievalService
from tesla_finrag.retrieval.in_memory import (
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
)

# =========================================================================
# 1. Query planning unit integration
# =========================================================================


class TestQueryPlanning:
    """Validate that the planner extracts the right structure from questions."""

    def test_period_extraction_single_quarter(self) -> None:
        periods = extract_periods("What was Tesla's revenue in Q3 2022?")
        assert periods == [date(2022, 9, 30)]

    def test_period_extraction_two_quarters(self) -> None:
        periods = extract_periods("Compare revenue between Q1 2022 and Q1 2023")
        assert sorted(periods) == [date(2022, 3, 31), date(2023, 3, 31)]

    def test_period_extraction_fiscal_year(self) -> None:
        periods = extract_periods("How did Tesla perform in FY2022?")
        assert periods == [date(2022, 12, 31)]

    def test_period_extraction_standalone_year(self) -> None:
        periods = extract_periods("What was Tesla's revenue in 2023?")
        assert periods == [date(2023, 12, 31)]

    def test_period_extraction_q4(self) -> None:
        periods = extract_periods("What was Tesla's revenue in Q4 2023?")
        assert periods == [date(2023, 12, 31)]

    def test_metric_extraction_revenue(self) -> None:
        metrics = extract_metrics("What was Tesla's total revenue in Q3 2022?")
        assert "us-gaap:Revenues" in metrics

    def test_metric_extraction_free_cash_flow(self) -> None:
        metrics = extract_metrics("Show me Tesla's free cash flow for FY2023")
        assert "custom:FreeCashFlow" in metrics

    def test_metric_extraction_multiple(self) -> None:
        metrics = extract_metrics("Compare gross profit to revenue")
        assert "us-gaap:GrossProfit" in metrics
        assert "us-gaap:Revenues" in metrics

    def test_classify_numeric_calculation(self) -> None:
        plan = RuleBasedQueryPlanner().plan("What was the total revenue in Q3 2022?")
        assert plan.query_type == QueryType.NUMERIC_CALCULATION
        assert plan.needs_calculation is True
        assert "us-gaap:Revenues" in plan.required_concepts
        assert date(2022, 9, 30) in plan.required_periods

    def test_classify_narrative_compare(self) -> None:
        plan = RuleBasedQueryPlanner().plan("What risk factors did Tesla discuss in their filings?")
        assert plan.query_type == QueryType.NARRATIVE_COMPARE
        assert plan.needs_calculation is False

    def test_classify_table_lookup(self) -> None:
        plan = RuleBasedQueryPlanner().plan("Show me the revenue breakdown by segment for Q1 2023")
        assert plan.query_type == QueryType.TABLE_LOOKUP
        assert plan.needs_calculation is True

    def test_plan_has_keywords(self) -> None:
        plan = RuleBasedQueryPlanner().plan("What was Tesla's automotive revenue in Q1 2023?")
        assert len(plan.retrieval_keywords) > 0
        assert "tesla's" in plan.retrieval_keywords or "tesla" in plan.retrieval_keywords

    def test_plan_sub_questions(self) -> None:
        plan = RuleBasedQueryPlanner().plan("What was revenue in Q3 2022?")
        assert plan.sub_questions == ["What was revenue in Q3 2022?"]


# =========================================================================
# 2. Text-only (narrative) question integration
# =========================================================================


class TestTextOnlyQuestions:
    """Integration tests for narrative / text-only questions."""

    def test_risk_factor_retrieval(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        corpus_repo: InMemoryCorpusRepository,
    ) -> None:
        """A risk-factor question should retrieve section chunks with risk content."""
        plan = planner.plan("What risk factors did Tesla discuss in their 2022 filings?")
        bundle = retrieval_service.retrieve(plan)
        assert len(bundle.section_chunks) > 0
        # At least one chunk should mention risk
        texts = [c.text.lower() for c in bundle.section_chunks]
        assert any("risk" in t for t in texts)

    def test_narrative_answer_has_citations(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """A narrative question should produce an answer with citations."""
        plan = planner.plan("What supply chain challenges did Tesla mention?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        assert answer.status == AnswerStatus.OK
        assert len(answer.citations) > 0
        assert "Tesla" in answer.answer_text

    def test_narrative_answer_confidence(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Narrative answers should have a non-zero confidence score."""
        plan = planner.plan("What operational efficiency initiatives did Tesla describe?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        assert answer.confidence is not None
        assert answer.confidence > 0.0

    def test_narrative_answer_text_format(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Narrative answers should include section titles in brackets."""
        plan = planner.plan("What did Tesla's management discuss about pricing strategy?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        # Narrative answer text should reference section titles
        assert "SEC filings" in answer.answer_text


# =========================================================================
# 3. Numeric question integration
# =========================================================================


class TestNumericQuestions:
    """Integration tests for numeric / calculation questions."""

    def test_single_period_revenue_lookup(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """A single-period revenue question should return a specific value."""
        plan = planner.plan("What was Tesla's total revenue in Q3 2022?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert answer.calculation_trace
        # Revenue for Q3 2022 = 21,454 * 1,000,000
        assert any("21,454" in line for line in answer.calculation_trace)

    def test_period_over_period_change(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Comparing two periods should produce a percentage change calculation."""
        plan = planner.plan("How did Tesla's revenue change from Q1 2022 to Q1 2023?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0
        # Should have a percentage change step
        trace_text = " ".join(answer.calculation_trace)
        assert "%" in trace_text or "change" in trace_text.lower()

    def test_multi_period_ranking(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """A ranking question across multiple periods should produce ranked trace."""
        plan = planner.plan(
            "Which quarter had the highest revenue in 2022? Q1 2022, Q2 2022, Q3 2022"
        )
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert len(answer.calculation_trace) > 0

    def test_calculator_aggregate_sum(
        self,
        calculator: StructuredCalculator,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """Sum aggregation should produce the correct total."""
        facts = facts_repo.get_facts(concept="us-gaap:Revenues")
        result, trace = calculator.aggregate(facts, "us-gaap:Revenues", CalcOp.SUM)
        # Sum of all revenue values: 18756+16934+21454+81462+23329+24927+23350+96773
        # All values have scale=1_000_000, so values are already in millions
        expected = sum(f.value * f.scale for f in facts if f.concept == "us-gaap:Revenues")
        assert result == pytest.approx(expected)
        assert len(trace) > 1

    def test_calculator_period_over_period(
        self,
        calculator: StructuredCalculator,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """Period-over-period should compute the correct change."""
        facts = facts_repo.get_facts(concept="us-gaap:Revenues")
        result, trace = calculator.period_over_period(
            facts,
            "us-gaap:Revenues",
            date(2022, 3, 31),
            date(2023, 3, 31),
            as_percent=True,
        )
        # Q1 2022: 18,756M, Q1 2023: 23,329M
        # pct = (23329 - 18756) / 18756 * 100
        expected_pct = ((23_329.0 - 18_756.0) / 18_756.0) * 100
        assert result == pytest.approx(expected_pct)
        assert any("%" in line for line in trace)

    def test_calculator_rank(
        self,
        calculator: StructuredCalculator,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """Ranking should return the highest value first."""
        # Get just quarterly revenue facts (not annual)
        all_facts = facts_repo.get_facts(concept="us-gaap:Revenues")
        quarterly = [f for f in all_facts if f.period_end.month != 12 or f.period_end.day != 31]
        result, trace = calculator.rank(quarterly, "us-gaap:Revenues")
        # Highest quarterly: Q2 2023 = 24,927M
        values = sorted(
            [f.value * f.scale for f in quarterly if f.concept == "us-gaap:Revenues"],
            reverse=True,
        )
        assert result == pytest.approx(values[0])

    def test_calculator_compute_ratio(
        self,
        calculator: StructuredCalculator,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """Ratio computation should divide correctly."""
        facts = facts_repo.get_facts(
            concept="us-gaap:GrossProfit", period_end=date(2022, 3, 31)
        ) + facts_repo.get_facts(concept="us-gaap:Revenues", period_end=date(2022, 3, 31))
        result, trace = calculator.compute_ratio(
            facts,
            "us-gaap:GrossProfit",
            "us-gaap:Revenues",
            date(2022, 3, 31),
        )
        # 5539M / 18756M
        expected = (5_539.0 * 1_000_000) / (18_756.0 * 1_000_000)
        assert result == pytest.approx(expected)

    def test_calculator_dsl_sum(
        self,
        calculator: StructuredCalculator,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """The calculate() DSL should handle sum() expressions."""
        facts = facts_repo.get_facts(concept="us-gaap:Revenues")
        result, trace = calculator.calculate("sum(us-gaap:Revenues)", facts)
        expected = sum(f.value * f.scale for f in facts)
        assert result == pytest.approx(expected)


# =========================================================================
# 4. Text-plus-table question integration
# =========================================================================


class TestTextPlusTableQuestions:
    """Integration tests for questions that need both text and table evidence."""

    def test_table_lookup_retrieves_table_chunks(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
    ) -> None:
        """A segment breakdown question should retrieve table chunks."""
        plan = planner.plan("Show me the revenue breakdown by segment for Q1 2023")
        bundle = retrieval_service.retrieve(plan)
        # Should have table chunks about revenue
        assert len(bundle.table_chunks) > 0 or len(bundle.section_chunks) > 0

    def test_table_lookup_answer_format(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Table lookup answers should contain financial statement text."""
        plan = planner.plan("Show me the revenue breakdown by segment for Q1 2023")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        assert answer.status == AnswerStatus.OK
        assert (
            "financial statements" in answer.answer_text.lower()
            or "SEC filings" in answer.answer_text
        )

    def test_evidence_linker_enriches_with_facts(
        self,
        linker: EvidenceLinker,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """The linker should add facts for periods present in the bundle."""
        from uuid import uuid4

        # Create a minimal bundle with a section chunk from a known filing
        filings = corpus_repo.list_filings()
        q1_2022 = [f for f in filings if f.period_end == date(2022, 3, 31)][0]
        sections = corpus_repo.get_section_chunks(q1_2022.doc_id)

        bundle = EvidenceBundle(
            plan_id=uuid4(),
            section_chunks=sections[:1],
        )
        enriched = linker.link(
            bundle,
            required_concepts=["us-gaap:Revenues"],
            required_periods=[date(2022, 3, 31)],
        )
        # Should have linked in revenue facts
        assert len(enriched.facts) > 0
        revenue_facts = [f for f in enriched.facts if f.concept == "us-gaap:Revenues"]
        assert len(revenue_facts) > 0
        assert enriched.metadata.get("linked_facts_count", 0) > 0

    def test_evidence_linker_adds_table_chunks(
        self,
        linker: EvidenceLinker,
        corpus_repo: InMemoryCorpusRepository,
    ) -> None:
        """The linker should add table chunks that mention required concepts."""
        from uuid import uuid4

        filings = corpus_repo.list_filings()
        q1_2023 = [f for f in filings if f.period_end == date(2023, 3, 31)][0]
        sections = corpus_repo.get_section_chunks(q1_2023.doc_id)

        bundle = EvidenceBundle(
            plan_id=uuid4(),
            section_chunks=sections[:1],
        )
        enriched = linker.link(
            bundle,
            required_concepts=["us-gaap:Revenues"],
            required_periods=[date(2023, 3, 31)],
        )
        # Revenue tables should mention "revenue" in caption or raw_text
        assert len(enriched.table_chunks) > 0

    def test_hybrid_retrieval_metadata(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
    ) -> None:
        """Retrieval bundle metadata should contain debug information."""
        plan = planner.plan("What was Tesla's revenue in Q3 2022?")
        bundle = retrieval_service.retrieve(plan)
        assert "lexical_hits" in bundle.metadata
        assert "query_text" in bundle.metadata
        assert bundle.metadata["lexical_hits"] >= 0

    def test_hybrid_retrieval_preserves_empty_period_filter(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
    ) -> None:
        """Unknown periods should not fall back to an unfiltered corpus search."""
        plan = planner.plan("What was Tesla's revenue in Q4 2025?")
        bundle = retrieval_service.retrieve(plan)
        assert plan.required_periods == [date(2025, 12, 31)]
        assert bundle.section_chunks == []
        assert bundle.table_chunks == []
        assert bundle.facts == []
        assert bundle.metadata["doc_id_filter"] == []

    def test_combined_narrative_and_numeric(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """A hybrid question should produce an answer with both text and calculations."""
        plan = planner.plan(
            "What was Tesla's revenue in Q3 2022 and what factors contributed to it?"
        )
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert answer.status == AnswerStatus.OK
        assert len(answer.citations) > 0
        assert answer.confidence is not None
        assert answer.confidence > 0.0


# =========================================================================
# 5. Citation and grounding validation
# =========================================================================


class TestCitationGrounding:
    """Validate that answers carry proper grounding context."""

    def test_citations_reference_valid_filings(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
        corpus_repo: InMemoryCorpusRepository,
    ) -> None:
        """Each citation should reference a filing that exists in the corpus."""
        plan = planner.plan("What was Tesla's revenue in Q1 2023?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        for citation in answer.citations:
            filing = corpus_repo.get_filing(citation.doc_id)
            assert filing is not None, f"Citation references unknown doc_id: {citation.doc_id}"
            assert citation.filing_type == filing.filing_type
            assert citation.period_end == filing.period_end

    def test_citations_have_excerpts(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Citations should contain non-empty excerpts."""
        plan = planner.plan("What was Tesla's gross profit in Q2 2022?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)

        assert len(answer.citations) > 0
        for citation in answer.citations:
            assert citation.excerpt, "Citation must have a non-empty excerpt"

    def test_answer_plan_id_matches(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """The answer's plan_id should match the input query plan."""
        plan = planner.plan("What was revenue in FY2023?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        assert answer.plan_id == plan.plan_id

    def test_answer_includes_retrieval_debug(
        self,
        planner: RuleBasedQueryPlanner,
        retrieval_service: HybridRetrievalService,
        composer: GroundedAnswerComposer,
    ) -> None:
        """The answer payload should expose retrieval diagnostics for evaluation/UI use."""
        plan = planner.plan("What was Tesla's revenue in Q1 2023?")
        bundle = retrieval_service.retrieve(plan)
        answer = composer.answer(plan, bundle)
        assert answer.retrieval_debug["query_type"] == plan.query_type.value
        assert answer.retrieval_debug["fact_records_count"] == len(bundle.facts)

    def test_insufficient_evidence_for_unknown_topic(
        self,
        composer: GroundedAnswerComposer,
    ) -> None:
        """Questions with no matching evidence should report insufficient evidence."""

        plan = RuleBasedQueryPlanner().plan("What is the price of dark matter?")
        empty_bundle = EvidenceBundle(plan_id=plan.plan_id)
        answer = composer.answer(plan, empty_bundle)
        assert answer.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer.confidence == 0.0
        assert "Insufficient evidence" in answer.answer_text
