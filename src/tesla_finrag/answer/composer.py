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
    AnswerShape,
    AnswerStatus,
    CalculationIntent,
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

        For COMPOSITE questions (narrative + numeric), the two lanes are
        evaluated independently: a missing numeric lane does not discard
        available narrative evidence.

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
            original_query=plan.original_query,
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

        # Step 3b: Composite partial answer support (Phase C)
        # For COMPOSITE questions, if the numeric lane failed but
        # narrative section chunks are available, produce a partial
        # answer instead of short-circuiting.
        is_composite = plan.answer_shape == AnswerShape.COMPOSITE
        numeric_limitation_reasons: list[str] = []
        has_narrative = len(enriched.section_chunks) > 0

        if is_composite and limitation_reasons and has_narrative:
            # Move limitation_reasons aside — they apply to the numeric
            # lane only.  The narrative lane can still produce an answer.
            numeric_limitation_reasons = list(limitation_reasons)
            limitation_reasons = []

        # Step 4: Build citations
        citations = self._build_citations(enriched)

        # Step 5: Compose answer text
        if limitation_reasons:
            answer_text = self._compose_limitation_text(limitation_reasons)
        elif is_composite and numeric_limitation_reasons:
            # Composite partial answer: narrative + numeric limitation
            answer_text = self._compose_composite_text(
                plan, enriched, calc_result, calc_trace, numeric_limitation_reasons
            )
        else:
            answer_text = self._compose_text(plan, enriched, calc_result, calc_trace)

        # Step 6: Determine status and confidence
        if limitation_reasons:
            if period_incompatible:
                status = AnswerStatus.CALCULATION_ERROR
            else:
                status = AnswerStatus.INSUFFICIENT_EVIDENCE
            confidence = 0.0
        elif is_composite and numeric_limitation_reasons:
            # Composite partial: narrative succeeded, numeric limited
            status = AnswerStatus.OK
            confidence = 0.5  # partial confidence
        else:
            status, confidence = self._assess_quality(enriched, plan)

        # Step 7: Build retrieval debug info with limitation reasons
        # Build ground-truth evidence fields for structured evaluation.
        # retrieved_fact_concepts: deduplicated concepts actually in the
        #   enriched evidence bundle (NOT planner-requested concepts).
        # fact_concepts_by_period: period -> concepts mapping so the judge
        #   can verify per-period coverage.
        retrieved_fact_concepts = sorted({f.concept for f in enriched.facts})
        fact_concepts_by_period: dict[str, list[str]] = {}
        for fact in enriched.facts:
            period_key = str(fact.period_end)
            fact_concepts_by_period.setdefault(period_key, [])
            if fact.concept not in fact_concepts_by_period[period_key]:
                fact_concepts_by_period[period_key].append(fact.concept)

        all_limitation_reasons = limitation_reasons or numeric_limitation_reasons

        retrieval_debug = {
            "section_chunks_count": len(enriched.section_chunks),
            "table_chunks_count": len(enriched.table_chunks),
            "fact_records_count": len(enriched.facts),
            "retrieval_scores": enriched.retrieval_scores,
            "query_type": plan.query_type.value,
            "calculation_intent": (
                plan.calculation_intent.value if plan.calculation_intent is not None else None
            ),
            "required_periods": [str(p) for p in plan.required_periods],
            "required_concepts": plan.required_concepts,
            "period_semantics": plan.period_semantics,
            "limitation_reasons": all_limitation_reasons,
            "numeric_limitation_reasons": numeric_limitation_reasons,
            "missing_periods": missing_periods,
            "missing_concepts_by_period": missing_concepts_by_period,
            "missing_narrative_periods": missing_narrative_periods,
            "retrieved_fact_concepts": retrieved_fact_concepts,
            "fact_concepts_by_period": fact_concepts_by_period,
            "is_composite": is_composite,
            "table_fallback_count": enriched.metadata.get("table_fallback_count", 0),
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
        """Run calculations based on the query plan and available facts.

        When ``plan.calculation_intent`` is set (Phase B), uses explicit
        intent-based routing for deterministic behaviour.  Otherwise falls
        back to the legacy ``len(required_concepts)`` heuristic.
        """
        if plan.calculation_intent is not None:
            return self._run_intent_based(plan, facts)
        return self._run_legacy_routing(plan, facts)

    # -- Intent-based routing (Phase B) ------------------------------------

    def _run_intent_based(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[float | None, list[str]]:
        """Dispatch to the correct calculator method based on explicit intent."""
        intent = plan.calculation_intent
        operands = plan.calculation_operands

        if intent == CalculationIntent.PCT_CHANGE:
            return self._intent_pct_change(plan, facts, operands)
        if intent == CalculationIntent.RATIO:
            return self._intent_ratio(plan, facts, operands)
        if intent == CalculationIntent.DIFFERENCE:
            return self._intent_difference(plan, facts, operands)
        if intent == CalculationIntent.RANK:
            return self._intent_rank(plan, facts, operands)
        if intent == CalculationIntent.STEP_TRACE:
            return self._intent_step_trace(plan, facts)
        # LOOKUP (default)
        return self._intent_lookup(plan, facts)

    def _intent_pct_change(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
        operands: list,
    ) -> tuple[float | None, list[str]]:
        """Handle PCT_CHANGE intent: period-over-period percentage change."""
        base_ops = [o for o in operands if o.role == "base"]
        target_ops = [o for o in operands if o.role == "target"]
        if base_ops and target_ops:
            concept = base_ops[0].concept
            period_a = base_ops[0].period
            period_b = target_ops[0].period
            if period_a and period_b:
                return self._calculator.period_over_period(
                    facts, concept, period_a, period_b, as_percent=True
                )
        # Fallback: use sorted required_periods
        if plan.required_concepts and len(plan.required_periods) >= 2:
            concept = plan.required_concepts[0]
            periods = sorted(plan.required_periods)
            return self._calculator.period_over_period(
                facts, concept, periods[0], periods[-1], as_percent=True
            )
        return None, ["PCT_CHANGE: insufficient operands"]

    def _intent_ratio(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
        operands: list,
    ) -> tuple[float | None, list[str]]:
        """Handle RATIO intent: compute numerator / denominator."""
        ratio_pairs = self._ratio_pairs_from_operands(plan, operands)
        if ratio_pairs:
            ratio_results: list[tuple[date | None, float]] = []
            trace: list[str] = []
            for period, numerator, denominator in ratio_pairs:
                ratio, pair_trace = self._calculator.compute_ratio(
                    facts,
                    numerator,
                    denominator,
                    period,
                )
                ratio_results.append((period, ratio))
                trace.extend(pair_trace)
                if self._display_ratio_as_percent(plan):
                    if period is None:
                        trace.append(f"Percentage: {ratio * 100:,.2f}%")
                    else:
                        trace.append(f"Percentage ({period}): {ratio * 100:,.2f}%")

            if len(ratio_results) >= 2 and plan.answer_shape == AnswerShape.COMPARISON:
                first_period, first_ratio = ratio_results[0]
                last_period, last_ratio = ratio_results[-1]
                delta_percentage_points = (last_ratio - first_ratio) * 100
                trace.append(
                    "Change in ratio: "
                    f"{last_ratio * 100:,.2f}% ({last_period}) - "
                    f"{first_ratio * 100:,.2f}% ({first_period}) = "
                    f"{delta_percentage_points:,.2f} percentage points"
                )
                return delta_percentage_points, trace

            final_ratio = ratio_results[-1][1]
            if self._display_ratio_as_percent(plan):
                return final_ratio * 100, trace
            return final_ratio, trace

        # Fallback: first two required_concepts
        if len(plan.required_concepts) >= 2:
            period = plan.required_periods[0] if plan.required_periods else None
            ratio, trace = self._calculator.compute_ratio(
                facts,
                plan.required_concepts[0],
                plan.required_concepts[1],
                period,
            )
            if self._display_ratio_as_percent(plan):
                trace.append(f"Percentage: {ratio * 100:,.2f}%")
                return ratio * 100, trace
            return ratio, trace
        return None, ["RATIO: insufficient operands"]

    def _intent_difference(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
        operands: list,
    ) -> tuple[float | None, list[str]]:
        """Handle DIFFERENCE intent: absolute change between periods."""
        base_ops = [o for o in operands if o.role == "base"]
        target_ops = [o for o in operands if o.role == "target"]
        if base_ops and target_ops:
            concept = base_ops[0].concept
            period_a = base_ops[0].period
            period_b = target_ops[0].period
            if period_a and period_b:
                return self._calculator.period_over_period(
                    facts, concept, period_a, period_b, as_percent=False
                )
        # Fallback
        if plan.required_concepts and len(plan.required_periods) >= 2:
            concept = plan.required_concepts[0]
            periods = sorted(plan.required_periods)
            return self._calculator.period_over_period(
                facts, concept, periods[0], periods[-1], as_percent=False
            )
        return None, ["DIFFERENCE: insufficient operands"]

    def _intent_rank(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
        operands: list,
    ) -> tuple[float | None, list[str]]:
        """Handle RANK intent: rank periods by concept value."""
        ratio_pairs = self._ratio_pairs_from_operands(plan, operands)
        if ratio_pairs:
            ranked_periods: list[tuple[date | None, float]] = []
            ratio_trace: list[str] = []
            for period, numerator, denominator in ratio_pairs:
                ratio, trace = self._calculator.compute_ratio(
                    facts,
                    numerator,
                    denominator,
                    period,
                )
                ratio_trace.extend(trace)
                ranked_periods.append((period, ratio))

            ranked_periods.sort(key=lambda item: item[1], reverse=True)
            trace = ["Ranking derived ratio (highest to lowest):"]
            for index, (period, ratio) in enumerate(ranked_periods, 1):
                label = f"{period}" if period is not None else "unscoped"
                if self._display_ratio_as_percent(plan):
                    trace.append(f"  {index}. {ratio * 100:,.2f}% (period ending {label})")
                else:
                    trace.append(f"  {index}. {ratio:,.4f} (period ending {label})")
            trace.extend(ratio_trace)
            top_ratio = ranked_periods[0][1]
            if self._display_ratio_as_percent(plan):
                return top_ratio * 100, trace
            return top_ratio, trace

        concept = operands[0].concept if operands else None
        if concept is None and plan.required_concepts:
            concept = plan.required_concepts[0]
        if concept:
            return self._calculator.rank(facts, concept)
        return None, ["RANK: no concept specified"]

    def _intent_step_trace(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[float | None, list[str]]:
        """Handle STEP_TRACE intent: detailed lookup with step-by-step output."""
        minuend_ops = [o for o in plan.calculation_operands if o.role == "minuend"]
        subtrahend_ops = [o for o in plan.calculation_operands if o.role == "subtrahend"]
        result_ops = [o for o in plan.calculation_operands if o.role == "result"]
        if minuend_ops and subtrahend_ops:
            steps: list[str] = []
            final_result: float | None = None
            periods = self._period_order_for_lookup(plan)
            for period in periods:
                minuend = self._lookup_fact_value(facts, minuend_ops[0].concept, period)
                subtrahend = self._lookup_fact_value(facts, subtrahend_ops[0].concept, period)
                if minuend is None or subtrahend is None:
                    steps.append(f"STEP_TRACE: missing operands for period {period or 'unscoped'}")
                    continue
                computed = minuend - subtrahend
                final_result = computed
                steps.append(
                    f"Operating cash flow ({period}): {minuend:,.2f}"
                    if period is not None
                    else f"Operating cash flow: {minuend:,.2f}"
                )
                steps.append(
                    f"Capital expenditure ({period}): {subtrahend:,.2f}"
                    if period is not None
                    else f"Capital expenditure: {subtrahend:,.2f}"
                )
                steps.append(
                    f"Free cash flow ({period}): {minuend:,.2f} - {subtrahend:,.2f} = "
                    f"{computed:,.2f}"
                    if period is not None
                    else f"Free cash flow: {minuend:,.2f} - {subtrahend:,.2f} = {computed:,.2f}"
                )
                if result_ops:
                    grounded_result = self._lookup_fact_value(facts, result_ops[0].concept, period)
                    if grounded_result is not None:
                        detail = (
                            "matches" if abs(grounded_result - computed) < 1e-6 else "differs from"
                        )
                        steps.append(
                            f"Grounded FCF fact ({period}): {grounded_result:,.2f} "
                            f"({detail} computed result)"
                            if period is not None
                            else f"Grounded FCF fact: {grounded_result:,.2f} "
                            f"({detail} computed result)"
                        )
            if final_result is not None:
                return final_result, steps
        return self._intent_lookup(plan, facts)

    def _intent_lookup(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[float | None, list[str]]:
        """Handle LOOKUP intent: direct fact retrieval."""
        all_trace: list[str] = []
        result: float | None = None
        seen_concepts: set[str] = set()
        concept_order: list[str] = []
        for operand in plan.calculation_operands:
            if (
                operand.role in {"primary", "result", "target"}
                and operand.concept not in seen_concepts
            ):
                concept_order.append(operand.concept)
                seen_concepts.add(operand.concept)
        for concept in plan.required_concepts:
            if concept not in seen_concepts:
                concept_order.append(concept)
                seen_concepts.add(concept)

        for concept in concept_order:
            for period in self._period_order_for_lookup(plan):
                match = next(
                    (
                        f
                        for f in facts
                        if f.concept == concept and (period is None or f.period_end == period)
                    ),
                    None,
                )
                if match:
                    val = match.value * match.scale
                    all_trace.append(
                        f"{match.label}: {val:,.2f} {match.unit} (period ending {match.period_end})"
                    )
                    if result is None:
                        result = val
                else:
                    all_trace.append(f"Missing {concept} for period {period}")
        return result, all_trace

    @staticmethod
    def _display_ratio_as_percent(plan: QueryPlan) -> bool:
        lower = plan.original_query.lower()
        return "margin" in lower or "percent" in lower or "percentage" in lower

    @staticmethod
    def _period_order_for_lookup(plan: QueryPlan) -> list[date | None]:
        return list(plan.required_periods) if plan.required_periods else [None]

    @staticmethod
    def _lookup_fact_value(
        facts: list[FactRecord],
        concept: str,
        period: date | None,
    ) -> float | None:
        for fact in facts:
            if fact.concept != concept:
                continue
            if period is not None and fact.period_end != period:
                continue
            return fact.value * fact.scale
        return None

    @staticmethod
    def _ratio_pairs_from_operands(
        plan: QueryPlan,
        operands: list,
    ) -> list[tuple[date | None, str, str]]:
        num_ops = [o for o in operands if o.role == "numerator"]
        den_ops = [o for o in operands if o.role == "denominator"]
        if not num_ops or not den_ops:
            return []

        periods = {
            *(o.period for o in num_ops if o.period is not None),
            *(o.period for o in den_ops if o.period is not None),
        }
        if not periods:
            return [(None, num_ops[0].concept, den_ops[0].concept)]

        pairs: list[tuple[date | None, str, str]] = []
        ordered_periods = sorted(periods)
        for period in ordered_periods:
            numerator = next((o.concept for o in num_ops if o.period == period), num_ops[0].concept)
            denominator = next(
                (o.concept for o in den_ops if o.period == period),
                den_ops[0].concept,
            )
            pairs.append((period, numerator, denominator))

        if not ordered_periods and plan.required_periods:
            return [(plan.required_periods[0], num_ops[0].concept, den_ops[0].concept)]

        return pairs

    # -- Legacy routing (pre-Phase B fallback) -----------------------------

    def _run_legacy_routing(
        self,
        plan: QueryPlan,
        facts: list[FactRecord],
    ) -> tuple[float | None, list[str]]:
        """Legacy calculation routing based on len(required_concepts)."""
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
            for chunk in self._prioritize_section_chunks(plan, bundle.section_chunks, limit=3):
                excerpt = self._narrative_excerpt(plan, chunk.text, max_chars=300)
                parts.append(f"[{chunk.section_title}] {excerpt}")

        else:
            # Hybrid: combine narrative and facts
            if calc_trace or bundle.section_chunks:
                parts.append("Based on Tesla's SEC filings:\n")
            if calc_trace:
                for line in calc_trace:
                    parts.append(line)
            for chunk in self._prioritize_section_chunks(plan, bundle.section_chunks, limit=2):
                excerpt = self._narrative_excerpt(plan, chunk.text, max_chars=300)
                parts.append(f"\n[{chunk.section_title}] {excerpt}")

        if not parts:
            parts.append(
                "Insufficient evidence found to answer this question. "
                "The available filing data may not contain the relevant information."
            )

        return "\n".join(parts)

    def _compose_composite_text(
        self,
        plan: QueryPlan,
        bundle: EvidenceBundle,
        calc_result: float | None,
        calc_trace: list[str],
        numeric_limitations: list[str],
    ) -> str:
        """Compose answer for composite (narrative + numeric) questions.

        Includes available narrative evidence and any calculation results,
        followed by a limitation note for the numeric lane if it failed.
        """
        parts: list[str] = ["Based on Tesla's SEC filings:\n"]

        # Narrative lane: include section chunks
        for chunk in self._prioritize_section_chunks(plan, bundle.section_chunks, limit=3):
            excerpt = self._narrative_excerpt(plan, chunk.text, max_chars=300)
            parts.append(f"[{chunk.section_title}] {excerpt}")

        # Numeric lane: include calc results if available
        if calc_trace:
            parts.append("")
            for line in calc_trace:
                parts.append(line)
            if calc_result is not None:
                parts.append(f"\nResult: {calc_result:,.2f}")
        elif numeric_limitations:
            # Numeric lane failed — add limitation note
            parts.append("")
            parts.append(
                "Note: The numeric component of this question could not be "
                "fully grounded from the available financial data."
            )
            for reason in numeric_limitations:
                parts.append(f"  - {reason}")

        return "\n".join(parts)

    @staticmethod
    def _narrative_cues_from_query(query: str) -> list[str]:
        lower = query.lower()
        cues: list[str] = []
        for cue in (
            "supply chain",
            "risk factors",
            "risk",
            "competition",
            "raw material",
            "logistics",
            "semiconductor",
            "geopolitical",
        ):
            if cue in lower:
                cues.append(cue)
        return cues

    def _prioritize_section_chunks(
        self,
        plan: QueryPlan,
        chunks: list,
        *,
        limit: int,
    ) -> list:
        """Prioritize section chunks that match narrative cues in the query."""
        if not chunks:
            return []
        cues = self._narrative_cues_from_query(plan.original_query)
        if not cues:
            return chunks[:limit]

        scored = []
        for index, chunk in enumerate(chunks):
            text = chunk.text.lower()
            score = sum(1 for cue in cues if cue in text)
            scored.append((score, index, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [chunk for _, _, chunk in scored[:limit]]

    def _narrative_excerpt(
        self,
        plan: QueryPlan,
        text: str,
        *,
        max_chars: int = 300,
    ) -> str:
        """Extract an excerpt that prefers containing query narrative cues."""
        if len(text) <= max_chars:
            return text
        lower = text.lower()
        for cue in self._narrative_cues_from_query(plan.original_query):
            pos = lower.find(cue)
            if pos == -1:
                continue
            start = max(0, pos - 80)
            end = min(len(text), start + max_chars)
            return text[start:end]
        return text[:max_chars]

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
