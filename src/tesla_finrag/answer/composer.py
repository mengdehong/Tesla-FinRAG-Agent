"""Grounded answer composition service.

Assembles a final :class:`AnswerPayload` from a query plan and
evidence bundle, including citations, calculation steps, retrieval
debug data, and confidence cues.

Implements :class:`AnswerService`.
"""

from __future__ import annotations

from tesla_finrag.calculation.calculator import CalcOp, StructuredCalculator
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AnswerPayload,
    AnswerStatus,
    Citation,
    EvidenceBundle,
    FactRecord,
    QueryPlan,
    QueryType,
)
from tesla_finrag.repositories import CorpusRepository, FactsRepository
from tesla_finrag.services import AnswerService


class GroundedAnswerComposer(AnswerService):
    """Compose grounded answers with citations and calculation traces.

    This implementation uses template-based answer construction (no LLM)
    so the pipeline is fully testable without external API calls.  A
    production version would pass the evidence to a language model for
    fluent answer generation while keeping citations deterministic.

    Parameters:
        corpus_repo: Repository for filing metadata lookups.
        facts_repo: Repository for fact record access.
        calculator: Structured calculator for numeric operations.
        linker: Evidence linker for enriching evidence bundles.
    """

    def __init__(
        self,
        corpus_repo: CorpusRepository,
        facts_repo: FactsRepository,
        calculator: StructuredCalculator | None = None,
        linker: EvidenceLinker | None = None,
    ) -> None:
        self._corpus = corpus_repo
        self._facts = facts_repo
        self._calculator = calculator or StructuredCalculator()
        self._linker = linker or EvidenceLinker(corpus_repo, facts_repo)

    def answer(self, plan: QueryPlan, bundle: EvidenceBundle) -> AnswerPayload:
        """Generate a cited, structured answer.

        Steps:
        1. Enrich the evidence bundle via evidence linking.
        2. If calculation is needed, run the calculator.
        3. Build citations from the evidence.
        4. Compose the answer text from evidence and calculations.
        5. Compute confidence based on evidence quality.

        Args:
            plan: The structured query plan.
            bundle: Retrieved evidence bundle.

        Returns:
            A fully populated :class:`AnswerPayload`.
        """
        # Step 1: Enrich evidence
        enriched = self._linker.link(
            bundle,
            required_concepts=plan.required_concepts,
            required_periods=plan.required_periods,
        )

        # Step 2: Calculate if needed
        calc_trace: list[str] = []
        calc_result: float | None = None
        if plan.needs_calculation and enriched.facts:
            calc_result, calc_trace = self._run_calculations(plan, enriched.facts)

        # Step 3: Build citations
        citations = self._build_citations(enriched)

        # Step 4: Compose answer text
        answer_text = self._compose_text(plan, enriched, calc_result, calc_trace)

        # Step 5: Determine status and confidence
        status, confidence = self._assess_quality(enriched, plan)

        # Step 6: Build retrieval debug info (retained for logging/future use)
        retrieval_debug = {
            "section_chunks_count": len(enriched.section_chunks),
            "table_chunks_count": len(enriched.table_chunks),
            "fact_records_count": len(enriched.facts),
            "retrieval_scores": enriched.retrieval_scores,
            "query_type": plan.query_type.value,
            "required_periods": [str(p) for p in plan.required_periods],
            "required_concepts": plan.required_concepts,
            **enriched.metadata,
        }

        return AnswerPayload(
            plan_id=plan.plan_id,
            status=status,
            answer_text=answer_text,
            citations=citations,
            calculation_trace=calc_trace,
            retrieval_debug=retrieval_debug,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _run_calculations(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[float | None, list[str]]:
        """Run calculations based on the query plan and available facts."""
        all_trace: list[str] = []
        result: float | None = None

        if len(plan.required_concepts) == 1:
            concept = plan.required_concepts[0]
            if len(plan.required_periods) == 2:
                # Period-over-period comparison
                periods = sorted(plan.required_periods)
                result, trace = self._calculator.period_over_period(
                    facts, concept, periods[0], periods[1], as_percent=True
                )
                all_trace.extend(trace)
            elif len(plan.required_periods) >= 2:
                # Multi-period ranking
                result, trace = self._calculator.rank(facts, concept)
                all_trace.extend(trace)
            else:
                # Single concept aggregation or lookup
                matching = [f for f in facts if f.concept == concept]
                if len(matching) > 1:
                    result, trace = self._calculator.aggregate(facts, concept, CalcOp.SUM)
                    all_trace.extend(trace)
                elif matching:
                    result = matching[0].value * matching[0].scale
                    all_trace.append(
                        f"{matching[0].label}: {result:,.2f} {matching[0].unit} "
                        f"(period ending {matching[0].period_end})"
                    )
        elif len(plan.required_concepts) == 2:
            # Ratio between two concepts
            period = plan.required_periods[0] if plan.required_periods else None
            result, trace = self._calculator.compute_ratio(
                facts,
                plan.required_concepts[0],
                plan.required_concepts[1],
                period,
            )
            all_trace.extend(trace)
        elif plan.required_concepts:
            # Multiple concepts: look up each
            for concept in plan.required_concepts:
                matching = [f for f in facts if f.concept == concept]
                for fact in matching:
                    val = fact.value * fact.scale
                    all_trace.append(
                        f"{fact.label}: {val:,.2f} {fact.unit} (period ending {fact.period_end})"
                    )
                    if result is None:
                        result = val

        return result, all_trace

    def _build_citations(self, bundle: EvidenceBundle) -> list[Citation]:
        """Build citation objects from the evidence bundle."""
        citations: list[Citation] = []

        for chunk in bundle.section_chunks:
            filing = self._corpus.get_filing(chunk.doc_id)
            if filing:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        filing_type=filing.filing_type,
                        period_end=filing.period_end,
                        excerpt=chunk.text[:200] if chunk.text else "",
                    )
                )

        for chunk in bundle.table_chunks:
            filing = self._corpus.get_filing(chunk.doc_id)
            if filing:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        filing_type=filing.filing_type,
                        period_end=filing.period_end,
                        excerpt=chunk.caption[:200] if chunk.caption else chunk.raw_text[:200],
                    )
                )

        for fact in bundle.facts:
            filing = self._corpus.get_filing(fact.doc_id)
            if filing:
                citations.append(
                    Citation(
                        chunk_id=fact.fact_id,
                        doc_id=fact.doc_id,
                        filing_type=filing.filing_type,
                        period_end=filing.period_end,
                        excerpt=f"{fact.label}: {fact.value * fact.scale:,.2f} {fact.unit}",
                    )
                )

        return citations

    def _compose_text(
        self,
        plan: QueryPlan,
        bundle: EvidenceBundle,
        calc_result: float | None,
        calc_trace: list[str],
    ) -> str:
        """Compose the answer text from evidence and calculations."""
        total_evidence = len(bundle.section_chunks) + len(bundle.table_chunks) + len(bundle.facts)
        if total_evidence == 0 and not calc_trace:
            return (
                "Insufficient evidence found to answer this question. "
                "The available filing data may not contain the relevant information."
            )

        parts: list[str] = []

        if plan.query_type == QueryType.NUMERIC_CALCULATION and calc_trace:
            # Lead with the calculation result
            parts.append("Based on Tesla's SEC filings:\n")
            for line in calc_trace:
                parts.append(line)
            if calc_result is not None:
                parts.append(f"\nResult: {calc_result:,.2f}")

        elif plan.query_type == QueryType.TABLE_LOOKUP and bundle.table_chunks:
            parts.append("From Tesla's financial statements:\n")
            for chunk in bundle.table_chunks[:3]:
                if chunk.caption:
                    parts.append(f"Table: {chunk.caption}")
                if chunk.raw_text:
                    parts.append(chunk.raw_text[:500])

        elif plan.query_type == QueryType.NARRATIVE_COMPARE and bundle.section_chunks:
            parts.append("From Tesla's SEC filings:\n")
            for chunk in bundle.section_chunks[:3]:
                parts.append(f"[{chunk.section_title}] {chunk.text[:300]}")

        else:
            # Hybrid: combine narrative and facts
            if calc_trace or bundle.section_chunks:
                parts.append("Based on Tesla's SEC filings:\n")
            if calc_trace:
                for line in calc_trace:
                    parts.append(line)
            for chunk in bundle.section_chunks[:2]:
                parts.append(f"\n[{chunk.section_title}] {chunk.text[:300]}")

        if not parts:
            parts.append(
                "Insufficient evidence found to answer this question. "
                "The available filing data may not contain the relevant information."
            )

        return "\n".join(parts)

    def _assess_quality(
        self,
        bundle: EvidenceBundle,
        plan: QueryPlan,
    ) -> tuple[AnswerStatus, float]:
        """Assess answer quality and determine status and confidence."""
        total_evidence = len(bundle.section_chunks) + len(bundle.table_chunks) + len(bundle.facts)

        if total_evidence == 0:
            return AnswerStatus.INSUFFICIENT_EVIDENCE, 0.0

        # Base confidence from evidence quantity
        confidence = min(total_evidence / 5.0, 1.0)

        # Boost if we have matching facts for a numeric question
        if plan.needs_calculation:
            if bundle.facts:
                matching_concepts = sum(
                    1 for f in bundle.facts if f.concept in plan.required_concepts
                )
                if matching_concepts > 0:
                    confidence = min(confidence + 0.2, 1.0)
                else:
                    confidence *= 0.5

        # Reduce confidence if we have no section chunks for narrative questions
        if plan.query_type == QueryType.NARRATIVE_COMPARE and not bundle.section_chunks:
            confidence *= 0.3

        return AnswerStatus.OK, round(confidence, 2)
