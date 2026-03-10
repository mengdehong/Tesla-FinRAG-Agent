"""LLM-first query planner with rule-based fallback."""

from __future__ import annotations

from datetime import date
from typing import Any

from tesla_finrag.concepts import SemanticConceptResolver
from tesla_finrag.models import (
    AnswerShape,
    CalculationIntent,
    ConceptResolution,
    QueryPlan,
    QueryType,
    SemanticScope,
)
from tesla_finrag.planning.query_planner import (
    RuleBasedQueryPlanner,
    _build_composite_narrative_sub_query,
    _build_normalized_search_text,
    _build_operands_for_intent,
    _build_sub_queries,
    _detect_semantic_scope,
    _detect_step_trace,
    _infer_answer_shape,
    _infer_calculation_intent,
    _needs_decomposition,
    build_period_semantics_map,
    detect_query_language,
    extract_keywords,
)
from tesla_finrag.provider import GroundedAnswerProvider, ProviderError
from tesla_finrag.services import QueryPlanningService
from tesla_finrag.settings import AppSettings, get_settings

_PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_mentions": {"type": "array"},
        "required_periods": {"type": "array"},
        "query_type": {"type": "string"},
        "answer_shape": {"type": "string"},
        "calculation_intent": {"type": "string"},
        "semantic_scope": {"type": "string"},
        "planner_confidence": {"type": "number"},
    },
}


def _enum_or_default(enum_cls: type, value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


class LLMQueryPlanner(QueryPlanningService):
    """Use the provider for structured planning, then resolve concepts deterministically."""

    def __init__(
        self,
        *,
        provider: GroundedAnswerProvider | None = None,
        concept_resolver: SemanticConceptResolver | None = None,
        fallback: RuleBasedQueryPlanner | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self._provider = provider
        self._concept_resolver = concept_resolver
        self._fallback = fallback or RuleBasedQueryPlanner()
        self._settings = settings or get_settings()

    def plan(self, question: str) -> QueryPlan:
        fallback_plan = self._fallback.plan(question)
        if self._settings.planner_mode == "rule" or self._provider is None:
            return fallback_plan.model_copy(
                update={
                    "planner_mode": "rule",
                    "metric_mentions": fallback_plan.metric_mentions
                    or [concept.split(":")[-1] for concept in fallback_plan.required_concepts],
                }
            )

        structured = self._request_structured_plan(question)
        if structured is None:
            return fallback_plan.model_copy(update={"planner_mode": "llm_fallback"})

        confidence = self._coerce_confidence(structured.get("planner_confidence"))
        if confidence < self._settings.planner_min_confidence:
            return fallback_plan.model_copy(
                update={
                    "planner_mode": "llm_fallback",
                    "planner_confidence": confidence,
                }
            )

        query_language = detect_query_language(question)
        metric_mentions = self._coerce_metric_mentions(structured.get("metric_mentions"))
        period_values = self._pick_periods(
            self._coerce_periods(structured.get("required_periods")),
            fallback_plan.required_periods,
        )
        semantic_scope = _enum_or_default(
            SemanticScope,
            self._coerce_str(structured.get("semantic_scope")),
            _detect_semantic_scope(question),
        )
        period_semantics = build_period_semantics_map(period_values, question)
        resolutions = (
            self._concept_resolver.resolve_mentions(
                metric_mentions,
                exact_concepts=fallback_plan.required_concepts,
            )
            if self._concept_resolver and metric_mentions
            else []
        )
        provisional_answer_shape = _enum_or_default(
            AnswerShape,
            self._coerce_str(structured.get("answer_shape")),
            _infer_answer_shape(question, period_values, fallback_plan.required_concepts),
        )
        required_concepts = self._merge_required_concepts(
            fallback_plan=fallback_plan,
            resolutions=resolutions,
            answer_shape=provisional_answer_shape,
        )
        alternative_concepts = self._alternative_concepts(
            resolutions,
            fallback_plan.required_concepts,
        )
        normalized_query = _build_normalized_search_text(
            question,
            required_concepts,
            period_values,
            query_language=query_language,
        )
        keywords = extract_keywords(question, required_concepts, period_values)
        answer_shape = _enum_or_default(
            AnswerShape,
            self._coerce_str(structured.get("answer_shape")),
            _infer_answer_shape(question, period_values, required_concepts),
        )
        calculation_intent = _enum_or_default(
            CalculationIntent,
            self._coerce_str(structured.get("calculation_intent")),
            _infer_calculation_intent(
                question,
                required_concepts,
                period_values,
                margin_intent=None,
            ),
        )
        sub_queries = (
            _build_sub_queries(
                question,
                period_values,
                required_concepts,
                period_semantics,
                query_language=query_language,
                semantic_scope=semantic_scope,
            )
            if _needs_decomposition(question, period_values)
            else []
        )
        if answer_shape == AnswerShape.COMPOSITE and not any(
            not sub_query.target_concepts for sub_query in sub_queries
        ):
            sub_queries.append(
                _build_composite_narrative_sub_query(
                    question,
                    period_values,
                    period_semantics,
                    query_language=query_language,
                )
            )
        final_query_language = detect_query_language(question)
        return QueryPlan(
            original_query=question,
            query_language=final_query_language,
            normalized_query=normalized_query,
            planner_mode="llm",
            planner_confidence=confidence,
            query_type=_enum_or_default(
                QueryType,
                self._coerce_str(structured.get("query_type")),
                fallback_plan.query_type,
            ),
            semantic_scope=semantic_scope,
            sub_questions=[question],
            metric_mentions=metric_mentions,
            sub_queries=sub_queries,
            retrieval_keywords=keywords,
            required_periods=period_values,
            period_semantics=period_semantics,
            required_concepts=required_concepts,
            alternative_concepts=alternative_concepts,
            concept_resolutions=resolutions,
            needs_calculation=bool(required_concepts and period_values),
            calculation_intent=calculation_intent,
            calculation_operands=_build_operands_for_intent(
                calculation_intent,
                required_concepts,
                period_values,
                existing_operands=fallback_plan.calculation_operands,
            ),
            requires_step_trace=_detect_step_trace(question),
            answer_shape=answer_shape,
        )

    def _request_structured_plan(self, question: str) -> dict[str, object] | None:
        if self._provider is None:
            return None
        try:
            return self._provider.generate_structured_json(
                system_prompt=(
                    "You are a financial query planner. Extract a typed plan for a Tesla SEC "
                    "filing question. Leave unknown fields empty rather than guessing."
                ),
                user_prompt=(
                    f"Question: {question}\n"
                    "Return JSON with metric_mentions, required_periods (ISO dates when "
                    "possible), query_type, answer_shape, calculation_intent, "
                    "semantic_scope, planner_confidence."
                ),
                json_schema=_PLANNER_SCHEMA,
            )
        except ProviderError:
            return None

    @staticmethod
    def _coerce_str(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _coerce_metric_mentions(value: object) -> list[str]:
        mentions: list[str] = []
        raw_items: list[object]
        if isinstance(value, list):
            raw_items = list(value)
        elif isinstance(value, dict):
            raw_items = []
            for key, item in value.items():
                raw_items.append(str(key).replace("_", " "))
                raw_items.append(item)
        else:
            return []

        for item in raw_items:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.count("-") == 2:
                try:
                    date.fromisoformat(normalized)
                    continue
                except ValueError:
                    pass
            mentions.append(normalized)
        deduped: list[str] = []
        for mention in mentions:
            if mention not in deduped:
                deduped.append(mention)
        return deduped

    @staticmethod
    def _looks_like_period_end(candidate: date) -> bool:
        return (candidate.month, candidate.day) in {
            (3, 31),
            (6, 30),
            (9, 30),
            (12, 31),
        }

    @classmethod
    def _pick_periods(
        cls,
        structured_periods: list[date],
        fallback_periods: list[date],
    ) -> list[date]:
        if not structured_periods:
            return fallback_periods
        if fallback_periods and not all(
            cls._looks_like_period_end(period) for period in structured_periods
        ):
            return fallback_periods
        return structured_periods

    @staticmethod
    def _coerce_confidence(value: object) -> float:
        if isinstance(value, str):
            normalized = value.strip().lower()
            named_confidence = {
                "high": 0.9,
                "medium": 0.6,
                "low": 0.3,
            }
            if normalized in named_confidence:
                return named_confidence[normalized]
        try:
            if value is None:
                return 0.0
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _coerce_periods(value: object) -> list[date]:
        if not isinstance(value, list):
            return []
        periods: list[date] = []
        for item in value:
            if not isinstance(item, str):
                continue
            try:
                periods.append(date.fromisoformat(item))
            except ValueError:
                continue
        return sorted(set(periods))

    @staticmethod
    def _alternative_concepts(
        resolutions: list[ConceptResolution],
        fallback_concepts: list[str],
    ) -> list[str]:
        concepts = list(fallback_concepts)
        for resolution in resolutions:
            for candidate in resolution.candidates:
                if candidate.concept not in concepts:
                    concepts.append(candidate.concept)
        return concepts

    def _merge_required_concepts(
        self,
        *,
        fallback_plan: QueryPlan,
        resolutions: list[ConceptResolution],
        answer_shape: AnswerShape,
    ) -> list[str]:
        resolved_concepts = [
            resolution.concept
            for resolution in resolutions
            if resolution.accepted and resolution.concept is not None
        ]
        resolved_concepts = list(dict.fromkeys(resolved_concepts))
        fallback_concepts = list(dict.fromkeys(fallback_plan.required_concepts))

        if not resolved_concepts:
            return fallback_concepts
        if not fallback_concepts:
            return resolved_concepts

        if answer_shape == AnswerShape.COMPOSITE and not self._supports_fallback_concepts(
            resolved_concepts,
            fallback_concepts,
        ):
            return fallback_concepts

        merged = list(fallback_concepts)
        for concept in resolved_concepts:
            if concept not in merged:
                merged.append(concept)
        return merged

    def _supports_fallback_concepts(
        self,
        resolved_concepts: list[str],
        fallback_concepts: list[str],
    ) -> bool:
        for fallback in fallback_concepts:
            for resolved in resolved_concepts:
                if resolved == fallback:
                    return True
                if self._concept_resolver is None:
                    continue
                if resolved in self._concept_resolver.safe_equivalents_for(fallback):
                    return True
                if fallback in self._concept_resolver.safe_equivalents_for(resolved):
                    return True
        return False


class FastPathPlanner(QueryPlanningService):
    """Use the rule planner when it is confidently sufficient, otherwise fall back to LLM."""

    def __init__(
        self,
        *,
        rule_planner: RuleBasedQueryPlanner | None = None,
        llm_planner: LLMQueryPlanner | None = None,
    ) -> None:
        self._rule_planner = rule_planner or RuleBasedQueryPlanner()
        self._llm_planner = llm_planner or LLMQueryPlanner(fallback=self._rule_planner)

    def plan(self, question: str) -> QueryPlan:
        rule_plan = self._rule_planner.plan(question)
        if rule_plan.required_concepts and rule_plan.answer_shape != AnswerShape.COMPOSITE:
            return rule_plan.model_copy(
                update={
                    "planner_mode": "rule_fast_path",
                    "metric_mentions": rule_plan.metric_mentions
                    or [concept.split(":")[-1] for concept in rule_plan.required_concepts],
                }
            )
        llm_plan = self._llm_planner.plan(question)
        if not llm_plan.metric_mentions and rule_plan.metric_mentions:
            return llm_plan.model_copy(update={"metric_mentions": rule_plan.metric_mentions})
        return llm_plan
