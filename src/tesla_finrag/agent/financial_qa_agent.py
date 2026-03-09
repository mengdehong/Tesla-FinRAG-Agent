"""Bounded financial QA agent with repair memory and streaming events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from tesla_finrag.answer import GroundedAnswerComposer
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AgentAction,
    AgentActionType,
    AgentEvent,
    AgentEventType,
    AgentHaltReason,
    AgentIterationTrace,
    AnswerPayload,
    EvidenceBundle,
    FactRecord,
    QueryPlan,
)
from tesla_finrag.provider import GroundedAnswerProvider
from tesla_finrag.repositories import CorpusRepository, FactsRepository, RetrievalStore
from tesla_finrag.retrieval import HybridRetrievalService
from tesla_finrag.services import QueryPlanningService
from tesla_finrag.settings import AppSettings, get_settings
from tesla_finrag.tracing import new_trace_id, summarize_agent_trace


@dataclass
class AgentStateMemory:
    """Mutable state that prevents repetitive repair loops."""

    attempted_signatures: set[str] = field(default_factory=set)
    unresolved_history: list[str] = field(default_factory=list)
    failed_candidates: set[str] = field(default_factory=set)
    no_progress_streak: int = 0
    latest_missing_signature: str = ""
    traces: list[AgentIterationTrace] = field(default_factory=list)
    halt_reason: AgentHaltReason | None = None


class FinancialQaAgent:
    """Run a bounded plan-retrieve-assess-repair loop."""

    def __init__(
        self,
        *,
        planner: QueryPlanningService,
        corpus_repo: CorpusRepository,
        facts_repo: FactsRepository,
        retrieval_store: RetrievalStore | None = None,
        indexing_provider: object | None = None,
        provider: GroundedAnswerProvider | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self._planner = planner
        self._corpus_repo = corpus_repo
        self._facts_repo = facts_repo
        self._retrieval_store = retrieval_store
        self._indexing_provider = indexing_provider
        self._provider = provider
        self._settings = settings or get_settings()
        self._linker = EvidenceLinker(corpus_repo, facts_repo)
        self._composer = GroundedAnswerComposer(corpus_repo=corpus_repo, facts_repo=facts_repo)

    def run(self, question: str) -> tuple[QueryPlan, EvidenceBundle, AnswerPayload]:
        final: tuple[QueryPlan, EvidenceBundle, AnswerPayload] | None = None
        for event in self.run_stream(question):
            payload = event.payload
            if event.event_type == AgentEventType.ANSWER_COMPLETED:
                final = (
                    payload["plan"],
                    payload["bundle"],
                    payload["answer"],
                )
        if final is None:
            raise RuntimeError("FinancialQaAgent finished without producing an answer.")
        return final

    def run_stream(self, question: str):
        memory = AgentStateMemory()
        plan = self._planner.plan(question)
        yield AgentEvent(
            event_type=AgentEventType.PLAN_CREATED,
            iteration=1,
            payload={"planner_mode": plan.planner_mode, "plan": plan},
        )
        if plan.concept_resolutions:
            yield AgentEvent(
                event_type=AgentEventType.CONCEPTS_RESOLVED,
                iteration=1,
                payload={
                    "concept_resolutions": plan.concept_resolutions,
                    "alternative_concepts": plan.alternative_concepts,
                },
            )

        current_plan = plan
        latest_bundle = EvidenceBundle(plan_id=plan.plan_id)
        latest_linked = latest_bundle

        for iteration in range(1, self._settings.agent_max_iterations + 1):
            bundle = self._retrieval().retrieve(current_plan)
            latest_bundle = bundle
            yield AgentEvent(
                event_type=AgentEventType.RETRIEVAL_COMPLETED,
                iteration=iteration,
                payload={"bundle": bundle},
            )
            linked = self._link(bundle, current_plan)
            latest_linked = linked
            trace = self._build_iteration_trace(iteration, linked)
            memory.traces.append(trace)
            yield AgentEvent(
                event_type=AgentEventType.COVERAGE_ASSESSED,
                iteration=iteration,
                payload={
                    "missing_periods": trace.missing_periods,
                    "missing_concepts_by_period": trace.missing_concepts_by_period,
                },
            )
            if not trace.missing_periods:
                answer = self._finalize_answer(
                    current_plan,
                    bundle,
                    memory,
                    AgentHaltReason.SUCCESS,
                )
                yield AgentEvent(
                    event_type=AgentEventType.ANSWER_COMPLETED,
                    iteration=iteration,
                    payload={"plan": current_plan, "bundle": bundle, "answer": answer},
                )
                yield AgentEvent(
                    event_type=AgentEventType.HALTED,
                    iteration=iteration,
                    payload={"halt_reason": AgentHaltReason.SUCCESS.value},
                )
                return

            action = self._select_action(current_plan, linked, memory)
            if action is None:
                halt_reason = (
                    AgentHaltReason.PARTIAL
                    if current_plan.answer_shape and current_plan.answer_shape.value == "composite"
                    else AgentHaltReason.EXHAUSTED
                )
                answer = self._finalize_answer(current_plan, latest_bundle, memory, halt_reason)
                yield AgentEvent(
                    event_type=AgentEventType.ANSWER_COMPLETED,
                    iteration=iteration,
                    payload={"plan": current_plan, "bundle": latest_bundle, "answer": answer},
                )
                yield AgentEvent(
                    event_type=AgentEventType.HALTED,
                    iteration=iteration,
                    payload={"halt_reason": halt_reason.value},
                )
                return

            yield AgentEvent(
                event_type=AgentEventType.REPAIR_SELECTED,
                iteration=iteration,
                payload={"action": action},
            )
            current_plan, latest_bundle, progress = self._execute_action(
                current_plan,
                latest_bundle,
                latest_linked,
                action,
            )
            memory.attempted_signatures.add(action.signature)
            memory.no_progress_streak = 0 if progress else memory.no_progress_streak + 1
            memory.latest_missing_signature = self._missing_signature(latest_linked)
            memory.traces[-1] = memory.traces[-1].model_copy(
                update={
                    "selected_action": action,
                    "new_fact_count": len(latest_bundle.facts) - len(bundle.facts),
                    "new_table_count": len(latest_bundle.table_chunks) - len(bundle.table_chunks),
                    "new_section_count": (
                        len(latest_bundle.section_chunks) - len(bundle.section_chunks)
                    ),
                    "no_progress": not progress,
                }
            )
            yield AgentEvent(
                event_type=AgentEventType.REPAIR_COMPLETED,
                iteration=iteration,
                payload={"action": action, "progress": progress},
            )
            if memory.no_progress_streak >= 2:
                answer = self._finalize_answer(
                    current_plan,
                    latest_bundle,
                    memory,
                    AgentHaltReason.EXHAUSTED,
                )
                yield AgentEvent(
                    event_type=AgentEventType.ANSWER_COMPLETED,
                    iteration=iteration,
                    payload={"plan": current_plan, "bundle": latest_bundle, "answer": answer},
                )
                yield AgentEvent(
                    event_type=AgentEventType.HALTED,
                    iteration=iteration,
                    payload={"halt_reason": AgentHaltReason.EXHAUSTED.value},
                )
                return

        final_linked = self._link(latest_bundle, current_plan)
        halt_reason = (
            AgentHaltReason.SUCCESS
            if not final_linked.metadata.get("missing_periods")
            else AgentHaltReason.EXHAUSTED
        )
        answer = self._finalize_answer(
            current_plan,
            latest_bundle,
            memory,
            halt_reason,
        )
        yield AgentEvent(
            event_type=AgentEventType.ANSWER_COMPLETED,
            iteration=self._settings.agent_max_iterations,
            payload={"plan": current_plan, "bundle": latest_bundle, "answer": answer},
        )
        yield AgentEvent(
            event_type=AgentEventType.HALTED,
            iteration=self._settings.agent_max_iterations,
            payload={"halt_reason": halt_reason.value},
        )

    def _retrieval(self) -> HybridRetrievalService:
        def embed_fn(text: str) -> list[float]:
            if self._indexing_provider is None or not hasattr(
                self._indexing_provider, "embed_texts"
            ):
                return []
            vectors = self._indexing_provider.embed_texts([text])
            return vectors[0] if vectors else []

        return HybridRetrievalService(
            corpus_repo=self._corpus_repo,
            facts_repo=self._facts_repo,
            retrieval_store=self._retrieval_store,
            embed_fn=(embed_fn if self._retrieval_store is not None else None),
        )

    def _link(self, bundle: EvidenceBundle, plan: QueryPlan) -> EvidenceBundle:
        return self._linker.link(
            bundle,
            required_concepts=plan.required_concepts,
            required_periods=plan.required_periods,
            period_semantics=plan.period_semantics,
            original_query=plan.original_query,
            semantic_scope=plan.semantic_scope,
        )

    def _build_iteration_trace(
        self,
        iteration: int,
        linked: EvidenceBundle,
    ) -> AgentIterationTrace:
        return AgentIterationTrace(
            iteration=iteration,
            missing_periods=list(linked.metadata.get("missing_periods", [])),
            missing_concepts_by_period=dict(linked.metadata.get("missing_concepts_by_period", {})),
        )

    def _select_action(
        self,
        plan: QueryPlan,
        linked: EvidenceBundle,
        memory: AgentStateMemory,
    ) -> AgentAction | None:
        missing_periods = list(linked.metadata.get("missing_periods", []))
        missing_concepts_by_period = dict(linked.metadata.get("missing_concepts_by_period", {}))
        missing_concepts = sorted(
            {
                concept
                for concepts in missing_concepts_by_period.values()
                for concept in concepts
            }
        )
        for concept in missing_concepts:
            for alternative in plan.alternative_concepts:
                if alternative == concept:
                    continue
                signature = f"{AgentActionType.CONCEPT_REPAIR.value}:{concept}->{alternative}"
                if signature in memory.attempted_signatures:
                    continue
                return AgentAction(
                    action_type=AgentActionType.CONCEPT_REPAIR,
                    signature=signature,
                    detail=f"Try fallback concept {alternative} for missing {concept}.",
                    target_concepts=[concept, alternative],
                    target_periods=self._periods_from_keys(missing_periods),
                )

        for concept in missing_concepts:
            if not concept.startswith("dei:"):
                continue
            for missing_period in self._periods_from_keys(missing_periods):
                relaxed_period = self._closest_same_year_period(concept, missing_period)
                if relaxed_period is None or relaxed_period == missing_period:
                    continue
                signature = (
                    f"{AgentActionType.PERIOD_RELAXATION_REPAIR.value}:"
                    f"{concept}:{missing_period.isoformat()}->{relaxed_period.isoformat()}"
                )
                if signature in memory.attempted_signatures:
                    continue
                return AgentAction(
                    action_type=AgentActionType.PERIOD_RELAXATION_REPAIR,
                    signature=signature,
                    detail=(
                        "Relax filing-period matching for filing-level DEI concepts by "
                        "reusing the closest fact in the same calendar year."
                    ),
                    target_concepts=[concept],
                    target_periods=[missing_period, relaxed_period],
                )

        if missing_periods:
            signature = (
                f"{AgentActionType.TABLE_RETRIEVAL_REPAIR.value}:"
                f"{','.join(sorted(missing_periods))}:{','.join(missing_concepts)}"
            )
            if signature not in memory.attempted_signatures:
                return AgentAction(
                    action_type=AgentActionType.TABLE_RETRIEVAL_REPAIR,
                    signature=signature,
                    detail="Broaden evidence collection to all tables and supporting sections.",
                    target_concepts=missing_concepts,
                    target_periods=self._periods_from_keys(missing_periods),
                )

        if (
            missing_periods
            and self._settings.enable_llm_table_extraction
            and self._provider is not None
        ):
            signature = (
                f"{AgentActionType.LLM_TABLE_EXTRACTION.value}:"
                f"{','.join(sorted(missing_periods))}:{','.join(missing_concepts)}"
            )
            if signature not in memory.attempted_signatures:
                return AgentAction(
                    action_type=AgentActionType.LLM_TABLE_EXTRACTION,
                    signature=signature,
                    detail="Ask the provider to extract missing numeric evidence from table text.",
                    target_concepts=missing_concepts,
                    target_periods=self._periods_from_keys(missing_periods),
                )
        return None

    def _execute_action(
        self,
        plan: QueryPlan,
        bundle: EvidenceBundle,
        linked: EvidenceBundle,
        action: AgentAction,
    ) -> tuple[QueryPlan, EvidenceBundle, bool]:
        if action.action_type == AgentActionType.CONCEPT_REPAIR:
            old_concept, new_concept = action.target_concepts[:2]
            updated = [
                new_concept if concept == old_concept else concept
                for concept in plan.required_concepts
            ]
            next_plan = plan.model_copy(
                update={
                    "required_concepts": updated,
                    "alternative_concepts": [
                        concept
                        for concept in plan.alternative_concepts
                        if concept != new_concept
                    ],
                }
            )
            return next_plan, bundle, True

        if action.action_type == AgentActionType.PERIOD_RELAXATION_REPAIR:
            old_period, new_period = action.target_periods[:2]
            updated_periods = [
                new_period if period == old_period else period for period in plan.required_periods
            ]
            updated_semantics = dict(plan.period_semantics)
            old_key = old_period.isoformat()
            new_key = new_period.isoformat()
            if old_key in updated_semantics and new_key not in updated_semantics:
                updated_semantics[new_key] = updated_semantics[old_key]
            updated_semantics.pop(old_key, None)
            updated_sub_queries = [
                sub_query.model_copy(
                    update={
                        "target_period": (
                            new_period
                            if sub_query.target_period == old_period
                            else sub_query.target_period
                        )
                    }
                )
                for sub_query in plan.sub_queries
            ]
            next_plan = plan.model_copy(
                update={
                    "required_periods": updated_periods,
                    "period_semantics": updated_semantics,
                    "sub_queries": updated_sub_queries,
                }
            )
            return next_plan, bundle, True

        if action.action_type == AgentActionType.TABLE_RETRIEVAL_REPAIR:
            table_chunks = list(bundle.table_chunks)
            section_chunks = list(bundle.section_chunks)
            seen_tables = {chunk.chunk_id for chunk in table_chunks}
            seen_sections = {chunk.chunk_id for chunk in section_chunks}
            target_periods = set(action.target_periods)
            for filing in self._corpus_repo.list_filings():
                if target_periods and filing.period_end not in target_periods:
                    continue
                for table in self._corpus_repo.get_table_chunks(filing.doc_id):
                    if table.chunk_id not in seen_tables:
                        table_chunks.append(table)
                        seen_tables.add(table.chunk_id)
                for section in self._corpus_repo.get_section_chunks(filing.doc_id):
                    if section.chunk_id not in seen_sections:
                        section_chunks.append(section)
                        seen_sections.add(section.chunk_id)
            widened = bundle.model_copy(
                update={
                    "table_chunks": table_chunks,
                    "section_chunks": section_chunks,
                }
            )
            return plan, widened, len(table_chunks) > len(bundle.table_chunks)

        llm_facts = self._llm_extract_missing_facts(linked, action)
        enriched = bundle.model_copy(update={"facts": [*bundle.facts, *llm_facts]})
        return plan, enriched, bool(llm_facts)

    def _llm_extract_missing_facts(
        self,
        linked: EvidenceBundle,
        action: AgentAction,
    ) -> list[FactRecord]:
        if self._provider is None:
            return []
        extracted: list[FactRecord] = []
        period_by_iso = {period.isoformat(): period for period in action.target_periods}
        table_text = "\n\n".join(chunk.raw_text for chunk in linked.table_chunks[:8])
        if not table_text:
            return []
        for period_key, period in period_by_iso.items():
            for concept in action.target_concepts:
                payload = self._provider.generate_structured_json(
                    system_prompt=(
                        "Extract a numeric financial value from Tesla filing tables. "
                        "Return JSON with "
                        "value and label. If not found, return {\"value\": null}."
                    ),
                    user_prompt=(
                        f"Concept: {concept}\nPeriod: {period_key}\n\n"
                        f"Table text:\n{table_text}"
                    ),
                    json_schema={"type": "object", "properties": {"value": {}, "label": {}}},
                )
                value = payload.get("value")
                if value is None:
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                doc_id = (
                    linked.table_chunks[0].doc_id
                    if linked.table_chunks
                    else (linked.facts[0].doc_id if linked.facts else uuid4())
                )
                source_chunk_id = linked.table_chunks[0].chunk_id if linked.table_chunks else None
                extracted.append(
                    FactRecord(
                        fact_id=uuid4(),
                        doc_id=doc_id,
                        concept=concept,
                        label=str(payload.get("label") or concept),
                        value=numeric_value,
                        unit="USD",
                        scale=1,
                        period_start=date(period.year, 1, 1),
                        period_end=period,
                        is_instant=False,
                        source_chunk_id=source_chunk_id,
                    )
                )
        return extracted

    def _finalize_answer(
        self,
        plan: QueryPlan,
        bundle: EvidenceBundle,
        memory: AgentStateMemory,
        halt_reason: AgentHaltReason,
    ) -> AnswerPayload:
        answer = self._composer.answer(plan, bundle)
        trace_id = new_trace_id()
        answer.retrieval_debug["agent_halt_reason"] = halt_reason.value
        answer.retrieval_debug["agent_trace"] = [
            trace.model_dump(mode="json") for trace in memory.traces
        ]
        answer.retrieval_debug["agent_attempted_signatures"] = sorted(memory.attempted_signatures)
        answer.retrieval_debug["trace_id"] = trace_id
        answer.retrieval_debug["trace_summary"] = summarize_agent_trace(memory.traces)
        return answer

    @staticmethod
    def _missing_signature(linked: EvidenceBundle) -> str:
        missing_periods = ",".join(sorted(linked.metadata.get("missing_periods", [])))
        missing_concepts = linked.metadata.get("missing_concepts_by_period", {})
        return f"{missing_periods}|{missing_concepts}"

    @staticmethod
    def _periods_from_keys(keys: list[str]) -> list[date]:
        periods: list[date] = []
        for key in keys:
            try:
                periods.append(date.fromisoformat(key))
            except ValueError:
                continue
        return periods

    def _closest_same_year_period(self, concept: str, missing_period: date) -> date | None:
        candidates = [
            fact.period_end
            for fact in self._facts_repo.get_facts(concept=concept)
            if fact.period_end.year == missing_period.year
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda candidate: abs((candidate - missing_period).days),
        )
