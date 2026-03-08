"""Grounded answer composition service.

Assembles a final :class:`AnswerPayload` from a query plan and
evidence bundle, including citations, calculation steps, retrieval
debug data, and confidence cues.

When the required grounded evidence is missing, incomplete, or
semantically incompatible with the requested question, the payload
returns a limitation status with debug context explaining the reason.

Implements :class:`AnswerService`.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from tesla_finrag.calculation.calculator import (
    CalcOp,
    PeriodIncompatibleError,
    StructuredCalculator,
    classify_fact_period,
    derive_standalone_quarter,
)
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AnswerPayload,
    AnswerStatus,
    Citation,
    EvidenceBundle,
    FactRecord,
    PeriodSemantics,
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
        2. Check evidence sufficiency — bail early if coverage is lacking.
        3. If calculation is needed, run the calculator (handling incompatibility).
        4. Build citations from the evidence.
        5. Compose the answer text from evidence and calculations.
        6. Compute confidence based on evidence quality.

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
            period_semantics=plan.period_semantics if plan.period_semantics else None,
        )

        # Step 2: Check evidence sufficiency (per-period coverage)
        limitation_reasons: list[str] = []
        missing_periods = enriched.metadata.get("missing_periods", [])
        missing_concepts_by_period = enriched.metadata.get("missing_concepts_by_period", {})
        missing_narrative_periods: list[str] = []
        if missing_periods:
            missing_label = (
                "facts" if plan.needs_calculation or plan.required_concepts else "evidence"
            )
            limitation_reasons.append(
                f"Missing grounded {missing_label} for period(s): {', '.join(missing_periods)}"
            )
            for period_key, concepts in sorted(missing_concepts_by_period.items()):
                if concepts:
                    limitation_reasons.append(
                        f"Missing required concept(s) for {period_key}: {', '.join(concepts)}"
                    )
        if not plan.needs_calculation and plan.required_periods:
            missing_narrative_periods = self._missing_section_periods(
                plan.required_periods,
                enriched,
                excluded_periods=set(missing_periods),
            )
            if missing_narrative_periods:
                limitation_reasons.append(
                    "Missing supporting narrative evidence for period(s): "
                    f"{', '.join(missing_narrative_periods)}"
                )

        # Step 3: Calculate if needed
        calc_trace: list[str] = []
        calc_result: float | None = None
        period_incompatible = False
        if plan.needs_calculation and enriched.facts and not limitation_reasons:
            calc_facts, derivation_trace, derivation_errors = self._prepare_facts_for_calculation(
                plan,
                enriched.facts,
            )
            if derivation_errors:
                limitation_reasons.extend(derivation_errors)
                calc_trace = derivation_trace
            try:
                if not derivation_errors:
                    calc_result, calc_trace = self._run_calculations(plan, calc_facts)
                    calc_trace = derivation_trace + calc_trace
            except PeriodIncompatibleError as exc:
                period_incompatible = True
                limitation_reasons.append(str(exc))
                calc_trace = derivation_trace + [f"Period incompatibility: {exc}"]
                if exc.details:
                    limitation_reasons.append(
                        f"Semantics: {exc.details.get('semantics_a', '?')} "
                        f"vs {exc.details.get('semantics_b', '?')}"
                    )

        # Step 4: Build citations
        citations = self._build_citations(enriched)

        # Step 5: Compose answer text
        if limitation_reasons:
            answer_text = self._compose_limitation_text(limitation_reasons)
        else:
            answer_text = self._compose_text(plan, enriched, calc_result, calc_trace)

        # Step 6: Determine status and confidence
        if limitation_reasons:
            if period_incompatible:
                status = AnswerStatus.CALCULATION_ERROR
            else:
                status = AnswerStatus.INSUFFICIENT_EVIDENCE
            confidence = 0.0
        else:
            status, confidence = self._assess_quality(enriched, plan)

        # Step 7: Build retrieval debug info with limitation reasons
        retrieval_debug = {
            "section_chunks_count": len(enriched.section_chunks),
            "table_chunks_count": len(enriched.table_chunks),
            "fact_records_count": len(enriched.facts),
            "retrieval_scores": enriched.retrieval_scores,
            "query_type": plan.query_type.value,
            "required_periods": [str(p) for p in plan.required_periods],
            "required_concepts": plan.required_concepts,
            "period_semantics": plan.period_semantics,
            "limitation_reasons": limitation_reasons,
            "missing_periods": missing_periods,
            "missing_concepts_by_period": missing_concepts_by_period,
            "missing_narrative_periods": missing_narrative_periods,
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
            elif len(plan.required_periods) == 1:
                # Explicit single-period lookup (avoid summing supporting facts)
                target_period = plan.required_periods[0]
                match = next(
                    (f for f in facts if f.concept == concept and f.period_end == target_period),
                    None,
                )
                if match:
                    result = match.value * match.scale
                    all_trace.append(
                        f"{match.label}: {result:,.2f} {match.unit} "
                        f"(period ending {match.period_end})"
                    )
                else:
                    all_trace.append(f"Missing {concept} for required period {target_period}")
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

    def _prepare_facts_for_calculation(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[list[FactRecord], list[str], list[str]]:
        """Prepare facts for calculation, including required Q4 derivations.

        If a required period is explicitly requested as standalone Q4,
        but only cumulative FY data exists for a duration metric, derive Q4 using
        ``Q4 = FY - Q1 - Q2 - Q3``.
        """
        if not plan.required_periods or not plan.required_concepts:
            return list(facts), [], []

        prepared = list(facts)
        derivation_trace: list[str] = []
        derivation_errors: list[str] = []

        for concept in plan.required_concepts:
            for period in plan.required_periods:
                period_key = period.isoformat()
                target_semantics = plan.period_semantics.get(period_key)
                if target_semantics != PeriodSemantics.QUARTERLY_STANDALONE:
                    continue
                # Q4 standalone is represented by a 12/31 period end.
                if period.month != 12 or period.day != 31:
                    continue

                existing = next(
                    (f for f in prepared if f.concept == concept and f.period_end == period),
                    None,
                )
                if existing and classify_fact_period(existing) in (
                    PeriodSemantics.QUARTERLY_STANDALONE,
                    PeriodSemantics.DERIVED_STANDALONE,
                    PeriodSemantics.INSTANT,
                ):
                    continue
                if self._concept_uses_instant_facts(concept, prepared):
                    derivation_trace.append(
                        "Skipping standalone Q4 derivation for instant metric "
                        f"{concept} at {period}."
                    )
                    continue

                supporting_facts = self._build_supporting_facts_for_q4_derivation(
                    concept,
                    period.year,
                    prepared,
                )
                derived_value, trace = derive_standalone_quarter(
                    concept,
                    period.year,
                    4,
                    supporting_facts,
                )
                derivation_trace.extend(trace)

                if derived_value is None:
                    detail = trace[-1] if trace else "insufficient supporting facts"
                    derivation_errors.append(
                        f"Unable to derive standalone Q4 for {concept} at {period}: {detail}"
                    )
                    continue

                base = next((f for f in supporting_facts if f.concept == concept), None)
                doc_id = self._doc_id_for_period(period)
                if base is None or doc_id is None:
                    derivation_errors.append(
                        f"Unable to derive standalone Q4 for {concept} at {period}: "
                        "missing filing metadata"
                    )
                    continue

                prepared = [
                    f for f in prepared if not (f.concept == concept and f.period_end == period)
                ]
                prepared.append(
                    FactRecord(
                        doc_id=doc_id,
                        concept=concept,
                        label=f"{base.label} (derived Q4 standalone)",
                        value=derived_value,
                        unit=base.unit,
                        scale=1,
                        period_start=date(period.year, 10, 1),
                        period_end=period,
                        is_instant=False,
                        source_chunk_id=base.source_chunk_id,
                    )
                )
                derivation_trace.append(
                    f"Using derived standalone Q4 value for {concept} at {period}."
                )

        return prepared, derivation_trace, derivation_errors

    def _build_supporting_facts_for_q4_derivation(
        self,
        concept: str,
        year: int,
        facts: list[FactRecord],
    ) -> list[FactRecord]:
        """Collect FY/Q1/Q2/Q3 facts needed to derive standalone Q4."""
        period_ends = [
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        ]
        result = list(facts)
        seen = {(f.concept, f.period_end, f.doc_id, f.fact_id) for f in result}
        for period_end in period_ends:
            for fact in self._facts.get_facts(concept=concept, period_end=period_end):
                key = (fact.concept, fact.period_end, fact.doc_id, fact.fact_id)
                if key in seen:
                    continue
                seen.add(key)
                result.append(fact)
        return result

    def _doc_id_for_period(self, period_end: date) -> UUID | None:
        """Find a filing doc_id for the given period end date."""
        for filing in self._corpus.list_filings():
            if filing.period_end == period_end:
                return filing.doc_id
        return None

    def _concept_uses_instant_facts(
        self,
        concept: str,
        facts: list[FactRecord],
    ) -> bool:
        """Return whether a concept behaves like an instant metric."""
        relevant = [fact for fact in facts if fact.concept == concept]
        if any(fact.is_instant for fact in relevant):
            return True
        return any(fact.is_instant for fact in self._facts.get_facts(concept=concept))

    def _missing_section_periods(
        self,
        required_periods: list[date],
        bundle: EvidenceBundle,
        *,
        excluded_periods: set[str] | None = None,
    ) -> list[str]:
        """Return required periods that lack narrative section evidence."""
        excluded = excluded_periods or set()
        covered_periods = {
            filing.period_end.isoformat()
            for chunk in bundle.section_chunks
            if (filing := self._corpus.get_filing(chunk.doc_id)) is not None
        }
        return [
            period.isoformat()
            for period in required_periods
            if period.isoformat() not in covered_periods and period.isoformat() not in excluded
        ]

    @staticmethod
    def _required_fact_matches(plan: QueryPlan, bundle: EvidenceBundle) -> set[str]:
        if not plan.required_concepts:
            return set()
        return {fact.concept for fact in bundle.facts if fact.concept in plan.required_concepts}

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
        if plan.needs_calculation and plan.required_concepts:
            matching_concepts = self._required_fact_matches(plan, bundle)
            if not matching_concepts:
                return (
                    "Insufficient evidence found to answer this question. "
                    "The available filing data did not contain grounded numeric facts "
                    "for the requested metric."
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
        if plan.needs_calculation and plan.required_concepts:
            matching_concepts = self._required_fact_matches(plan, bundle)
            if not matching_concepts:
                return AnswerStatus.INSUFFICIENT_EVIDENCE, 0.0

        # Base confidence from evidence quantity
        confidence = min(total_evidence / 5.0, 1.0)

        # Boost if we have matching facts for a numeric question
        if plan.needs_calculation:
            if bundle.facts:
                matching_concepts = len(self._required_fact_matches(plan, bundle))
                if matching_concepts > 0:
                    confidence = min(confidence + 0.2, 1.0)
                else:
                    confidence *= 0.5

        # Reduce confidence if we have no section chunks for narrative questions
        if plan.query_type == QueryType.NARRATIVE_COMPARE and not bundle.section_chunks:
            confidence *= 0.3

        return AnswerStatus.OK, round(confidence, 2)

    @staticmethod
    def _compose_limitation_text(reasons: list[str]) -> str:
        """Compose a user-facing limitation message from a list of reasons.

        The text clearly states that the answer cannot be grounded and
        enumerates each specific limitation so the caller can understand
        what evidence was missing or incompatible.
        """
        header = "Unable to provide a fully grounded answer for this question."
        if len(reasons) == 1:
            return f"{header} {reasons[0]}"
        bullet_lines = "\n".join(f"- {r}" for r in reasons)
        return f"{header}\n{bullet_lines}"
