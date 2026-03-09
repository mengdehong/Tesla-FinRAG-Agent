"""Shared workbench pipeline for the Streamlit demo and evaluation runner.

This module wires the real planning -> retrieval -> answer pipeline over the
processed Tesla corpus loaded from ``data/processed/``.

The pipeline supports two provider modes:
- ``local`` (default): Ollama-backed local embeddings and grounded narration.
- ``openai-compatible``: remote OpenAI-compatible embeddings and grounded chat
  narration over the same processed corpus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

from tesla_finrag.agent import FinancialQaAgent
from tesla_finrag.concepts import (
    SemanticConceptResolver,
    build_companyfacts_catalog,
    default_companyfacts_path,
)
from tesla_finrag.i18n import response_language_directive
from tesla_finrag.models import (
    AnswerPayload,
    AnswerShape,
    AnswerStatus,
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    FilingType,
    QueryLanguage,
    QueryPlan,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.planning import FastPathPlanner, LLMQueryPlanner, RuleBasedQueryPlanner
from tesla_finrag.repositories import RetrievalStore
from tesla_finrag.retrieval import (
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
)
from tesla_finrag.settings import get_settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tesla_finrag.provider import GroundedAnswerProvider, ProviderInfo, TextEmbeddingProvider


# ---------------------------------------------------------------------------
# Provider mode enum
# ---------------------------------------------------------------------------


class ProviderMode(StrEnum):
    """Supported provider modes for the demo pipeline."""

    LOCAL = "local"
    OPENAI_COMPATIBLE = "openai-compatible"


# ---------------------------------------------------------------------------
# Filing scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingScope:
    """Optional filing filters applied before retrieval."""

    fiscal_years: tuple[int, ...] = ()
    filing_type: FilingType | None = None
    quarters: tuple[int, ...] = ()

    def matches(self, filing: FilingDocument) -> bool:
        if self.fiscal_years and filing.fiscal_year not in self.fiscal_years:
            return False
        if self.filing_type and filing.filing_type != self.filing_type:
            return False
        if (
            self.filing_type == FilingType.QUARTERLY
            and self.quarters
            and filing.fiscal_quarter not in self.quarters
        ):
            return False
        return True

    def as_metadata(self) -> dict[str, object]:
        return {
            "fiscal_years": list(self.fiscal_years),
            "filing_type": self.filing_type.value if self.filing_type else None,
            "quarters": [f"Q{quarter}" for quarter in self.quarters],
        }


# ---------------------------------------------------------------------------
# Demo data helpers
# ---------------------------------------------------------------------------


def _make_filing(
    form: FilingType,
    period_end: date,
    fiscal_year: int,
    fiscal_quarter: int | None,
    source: str,
) -> FilingDocument:
    filed_year = period_end.year + 1 if period_end.month == 12 else period_end.year
    filed_month = 1 if period_end.month == 12 else period_end.month + 1
    return FilingDocument(
        filing_type=form,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        accession_number=f"0000950170-{fiscal_year}-{str(period_end.month).zfill(2)}",
        filed_at=date(filed_year, filed_month, 15),
        source_path=source,
    )


def _mda_text(filing: FilingDocument) -> str:
    q_label = f"Q{filing.fiscal_quarter}" if filing.fiscal_quarter else "full year"
    if filing.fiscal_year == 2022 and filing.fiscal_quarter == 3:
        return (
            f"During {q_label} {filing.fiscal_year}, Tesla experienced significant "
            "supply chain challenges and semiconductor shortages that impacted "
            "production capacity. Despite these headwinds, total automotive revenue "
            "grew year-over-year driven by higher vehicle deliveries and increased "
            "average selling prices."
        )
    if filing.fiscal_year == 2023 and filing.fiscal_quarter == 1:
        return (
            f"In {q_label} {filing.fiscal_year}, Tesla implemented strategic price "
            "reductions across its vehicle lineup to stimulate demand and maintain "
            "market share. This pricing strategy resulted in lower gross margins "
            "compared to the prior quarter but drove record delivery volumes."
        )
    return (
        f"In {q_label} {filing.fiscal_year}, Tesla continued to expand its "
        "manufacturing capacity and delivery infrastructure. The company "
        "focused on operational efficiency and cost reduction initiatives "
        "while investing in new product development and energy storage solutions."
    )


def _risk_text(filing: FilingDocument) -> str:
    if filing.fiscal_year == 2022:
        return (
            "Risk factors include supply chain disruptions, semiconductor shortages, "
            "raw material cost increases, and geopolitical uncertainties. Competition "
            "in the electric vehicle market has intensified with traditional automakers "
            "increasing their EV offerings."
        )
    return (
        "Key risks include increasing competition in the EV market, potential "
        "impacts of pricing strategy on margins, regulatory changes, and "
        "macroeconomic conditions affecting consumer demand. Raw material costs "
        "and supply chain reliability remain ongoing concerns."
    )


def _revenue_rows(filing: FilingDocument) -> list[list[str]]:
    data = {
        (2022, 1): [["Automotive", "16,861"], ["Energy", "616"], ["Services", "1,279"]],
        (2022, 2): [["Automotive", "14,602"], ["Energy", "866"], ["Services", "1,466"]],
        (2022, 3): [["Automotive", "18,692"], ["Energy", "1,117"], ["Services", "1,645"]],
        (2022, None): [["Automotive", "71,462"], ["Energy", "3,909"], ["Services", "6,091"]],
        (2023, 1): [["Automotive", "19,963"], ["Energy", "1,529"], ["Services", "1,837"]],
        (2023, 2): [["Automotive", "21,268"], ["Energy", "1,509"], ["Services", "2,150"]],
        (2023, 3): [["Automotive", "19,625"], ["Energy", "1,559"], ["Services", "2,166"]],
        (2023, None): [["Automotive", "82,419"], ["Energy", "6,035"], ["Services", "8,319"]],
    }
    return data.get((filing.fiscal_year, filing.fiscal_quarter), [["Automotive", "0"]])


def _revenue_raw_text(filing: FilingDocument) -> str:
    rows = _revenue_rows(filing)
    lines = ["Segment | Revenue (millions)"]
    for row in rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def _seed_demo_repositories() -> tuple[InMemoryCorpusRepository, InMemoryFactsRepository]:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()

    filings = [
        _make_filing(
            FilingType.QUARTERLY, date(2022, 3, 31), 2022, 1, "data/raw/Tesla_2022_Q1_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2022, 6, 30), 2022, 2, "data/raw/Tesla_2022_Q2_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2022, 9, 30), 2022, 3, "data/raw/Tesla_2022_Q3_10-Q.pdf"
        ),
        _make_filing(
            FilingType.ANNUAL, date(2022, 12, 31), 2022, None, "data/raw/Tesla_2022_全年_10-K.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 3, 31), 2023, 1, "data/raw/Tesla_2023_Q1_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 6, 30), 2023, 2, "data/raw/Tesla_2023_Q2_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 9, 30), 2023, 3, "data/raw/Tesla_2023_Q3_10-Q.pdf"
        ),
        _make_filing(
            FilingType.ANNUAL, date(2023, 12, 31), 2023, None, "data/raw/Tesla_2023_全年_10-K.pdf"
        ),
    ]
    for filing in filings:
        corpus_repo.upsert_filing(filing)
        corpus_repo.upsert_section_chunk(
            SectionChunk(
                doc_id=filing.doc_id,
                section_title="Management Discussion and Analysis",
                text=_mda_text(filing),
                token_count=50,
                page_number=10,
            )
        )
        corpus_repo.upsert_section_chunk(
            SectionChunk(
                doc_id=filing.doc_id,
                section_title="Risk Factors",
                text=_risk_text(filing),
                token_count=40,
                page_number=20,
            )
        )
        corpus_repo.upsert_table_chunk(
            TableChunk(
                doc_id=filing.doc_id,
                section_title="Consolidated Statements of Operations",
                caption=f"Revenue breakdown for period ending {filing.period_end}",
                headers=["Segment", "Revenue (millions)"],
                rows=_revenue_rows(filing),
                raw_text=_revenue_raw_text(filing),
            )
        )

    fact_data = {
        "us-gaap:Revenues": {
            date(2022, 3, 31): ("Total Revenues", 18_756.0),
            date(2022, 6, 30): ("Total Revenues", 16_934.0),
            date(2022, 9, 30): ("Total Revenues", 21_454.0),
            date(2022, 12, 31): ("Total Revenues", 81_462.0),
            date(2023, 3, 31): ("Total Revenues", 23_329.0),
            date(2023, 6, 30): ("Total Revenues", 24_927.0),
            date(2023, 9, 30): ("Total Revenues", 23_350.0),
            date(2023, 12, 31): ("Total Revenues", 96_773.0),
        },
        "us-gaap:GrossProfit": {
            date(2022, 3, 31): ("Gross Profit", 5_539.0),
            date(2022, 6, 30): ("Gross Profit", 4_234.0),
            date(2022, 9, 30): ("Gross Profit", 5_382.0),
            date(2022, 12, 31): ("Gross Profit", 20_853.0),
            date(2023, 3, 31): ("Gross Profit", 4_511.0),
            date(2023, 6, 30): ("Gross Profit", 4_533.0),
            date(2023, 9, 30): ("Gross Profit", 4_178.0),
            date(2023, 12, 31): ("Gross Profit", 17_660.0),
        },
        "us-gaap:OperatingIncomeLoss": {
            date(2022, 3, 31): ("Operating Income", 3_600.0),
            date(2022, 6, 30): ("Operating Income", 2_464.0),
            date(2022, 9, 30): ("Operating Income", 3_688.0),
            date(2022, 12, 31): ("Operating Income", 13_656.0),
            date(2023, 3, 31): ("Operating Income", 2_664.0),
            date(2023, 6, 30): ("Operating Income", 2_399.0),
            date(2023, 9, 30): ("Operating Income", 1_764.0),
            date(2023, 12, 31): ("Operating Income", 8_891.0),
        },
        "custom:FreeCashFlow": {
            date(2022, 3, 31): ("Free Cash Flow", 2_228.0),
            date(2022, 6, 30): ("Free Cash Flow", 621.0),
            date(2022, 9, 30): ("Free Cash Flow", 3_297.0),
            date(2022, 12, 31): ("Free Cash Flow", 7_566.0),
            date(2023, 3, 31): ("Free Cash Flow", 441.0),
            date(2023, 6, 30): ("Free Cash Flow", 1_007.0),
            date(2023, 9, 30): ("Free Cash Flow", 848.0),
            date(2023, 12, 31): ("Free Cash Flow", 4_358.0),
        },
    }

    filings_by_period = {filing.period_end: filing for filing in filings}
    for concept, values in fact_data.items():
        for period_end, (label, value) in values.items():
            filing = filings_by_period[period_end]
            facts_repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept=concept,
                    label=label,
                    value=value,
                    unit="USD",
                    scale=1_000_000,
                    period_start=(
                        date(period_end.year, 1, 1) if filing.fiscal_quarter is None else None
                    ),
                    period_end=period_end,
                )
            )

    return corpus_repo, facts_repo


# ---------------------------------------------------------------------------
# Chunk text helpers (for embedding)
# ---------------------------------------------------------------------------


def _chunk_text(chunk: SectionChunk | TableChunk) -> str:
    """Extract the searchable text from a chunk for embedding."""
    if isinstance(chunk, SectionChunk):
        return chunk.text
    return chunk.raw_text


# ---------------------------------------------------------------------------
# WorkbenchPipeline
# ---------------------------------------------------------------------------


class WorkbenchPipeline:
    """Reusable plan -> retrieve -> answer pipeline over the processed corpus.

    Supports ``local`` (default) and ``openai-compatible`` provider modes.
    """

    def __init__(
        self,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
        *,
        provider_mode: ProviderMode = ProviderMode.LOCAL,
        provider: GroundedAnswerProvider | None = None,
        retrieval_store: RetrievalStore | None = None,
        indexing_provider: TextEmbeddingProvider | None = None,
    ) -> None:
        self._corpus_repo = corpus_repo
        self._facts_repo = facts_repo
        self._provider_mode = provider_mode
        self._provider = provider  # OpenAIProvider instance when remote
        self._retrieval_store = retrieval_store
        self._indexing_provider = indexing_provider
        settings = get_settings()
        self._concept_resolver = SemanticConceptResolver(
            build_companyfacts_catalog(default_companyfacts_path()),
            embedding_backend=indexing_provider,
            top_k=settings.concept_search_top_k,
            semantic_accept_score=settings.concept_semantic_accept_score,
            semantic_accept_gap=settings.concept_semantic_accept_gap,
            calibrated=settings.concept_resolution_calibrated,
            calibration_version=(
                f"{getattr(indexing_provider, 'info', None).embedding_model}"
                if indexing_provider is not None and hasattr(indexing_provider, "info")
                else "uncalibrated"
            ),
        )
        self._rule_planner = RuleBasedQueryPlanner()
        self._llm_planner = LLMQueryPlanner(
            provider=provider,
            concept_resolver=self._concept_resolver,
            fallback=self._rule_planner,
            settings=settings,
        )
        self._planner = FastPathPlanner(
            rule_planner=self._rule_planner,
            llm_planner=self._llm_planner,
        )

    @property
    def provider_mode(self) -> ProviderMode:
        return self._provider_mode

    @property
    def available_years(self) -> list[int]:
        return sorted({filing.fiscal_year for filing in self._corpus_repo.list_filings()})

    @property
    def available_quarters(self) -> list[int]:
        return sorted(
            {
                filing.fiscal_quarter
                for filing in self._corpus_repo.list_filings()
                if filing.fiscal_quarter is not None
            }
        )

    def answer_question(self, question: str, scope: FilingScope | None = None) -> AnswerPayload:
        _, _, answer = self.run(question, scope=scope)
        return answer

    def run_stream(
        self,
        question: str,
        scope: FilingScope | None = None,
        response_language: str | None = None,
    ):
        corpus_repo, facts_repo = self._scoped_repositories(scope)
        from tesla_finrag.provider import ProviderError

        if self._provider is None:
            if self._provider_mode == ProviderMode.LOCAL:
                raise ProviderError(
                    "local Ollama provider mode selected but no provider was configured. "
                    "Ensure Ollama is available or inject a provider."
                )
            raise ProviderError(
                "openai-compatible provider mode selected but no provider was configured. "
                "Set OPENAI_API_KEY in the environment."
            )

        agent = FinancialQaAgent(
            planner=self._planner,
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=self._retrieval_store,
            indexing_provider=self._indexing_provider,
            provider=self._provider,
        )
        yield from agent.run_stream(question)

    def run(
        self,
        question: str,
        scope: FilingScope | None = None,
        response_language: str | None = None,
    ) -> tuple[QueryPlan, EvidenceBundle, AnswerPayload]:
        corpus_repo, facts_repo = self._scoped_repositories(scope)
        from tesla_finrag.provider import ProviderError

        if self._provider is None:
            if self._provider_mode == ProviderMode.LOCAL:
                raise ProviderError(
                    "local Ollama provider mode selected but no provider was configured. "
                    "Ensure Ollama is available or inject a provider."
                )
            raise ProviderError(
                "openai-compatible provider mode selected but no provider was configured. "
                "Set OPENAI_API_KEY in the environment."
            )

        provider = self._provider
        provider_info = provider.info
        indexing_provider = self._indexing_provider
        agent = FinancialQaAgent(
            planner=self._planner,
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=self._retrieval_store,
            indexing_provider=indexing_provider,
            provider=provider,
        )
        plan, bundle, local_answer = agent.run(question)

        if local_answer.status != AnswerStatus.OK:
            local_answer.retrieval_debug.update(
                self._build_retrieval_debug(
                    provider_info=provider_info,
                    indexing_provider=indexing_provider,
                    bundle=bundle,
                    scope=scope,
                    available_filings=len(corpus_repo.list_filings()),
                    answer_provider="template-guardrail",
                )
            )
            return plan, bundle, local_answer

        # Now narrate the answer text using the remote chat model
        evidence_summary = self._build_evidence_summary(plan, bundle)
        effective_response_language = response_language or self._default_response_language(plan)
        try:
            remote_text = provider.generate_grounded_answer(
                question=plan.original_query,
                evidence=evidence_summary,
                calculation_trace=local_answer.calculation_trace or None,
                response_language=effective_response_language,
            )
        except ProviderError:
            logger.exception(
                "Provider-backed chat narration failed for mode %s",
                self._provider_mode.value,
            )
            raise

        local_answer_fallback_used = False
        if self._should_use_local_answer(plan, bundle, remote_text, local_answer):
            remote_text = local_answer.answer_text
            local_answer_fallback_used = True

        answer = AnswerPayload(
            plan_id=local_answer.plan_id,
            status=local_answer.status,
            answer_text=remote_text,
            citations=local_answer.citations,
            calculation_trace=local_answer.calculation_trace,
            retrieval_debug=local_answer.retrieval_debug,
            confidence=local_answer.confidence,
        )
        if local_answer_fallback_used:
            answer.retrieval_debug["local_answer_fallback_used"] = True
        answer.retrieval_debug.update(
            self._build_retrieval_debug(
                provider_info=provider_info,
                indexing_provider=indexing_provider,
                bundle=bundle,
                scope=scope,
                available_filings=len(corpus_repo.list_filings()),
                answer_provider=provider_info.provider_name,
            )
        )
        return plan, bundle, answer

    def _build_retrieval_debug(
        self,
        *,
        provider_info: ProviderInfo,
        indexing_provider: TextEmbeddingProvider | None,
        bundle: EvidenceBundle,
        scope: FilingScope | None,
        available_filings: int,
        answer_provider: str,
    ) -> dict[str, object]:
        debug: dict[str, object] = {
            "provider_mode": self._provider_mode.value,
            "answer_provider": answer_provider,
            "answer_model": provider_info.answer_model,
            "chat_model": provider_info.answer_model,
            "answer_base_url": provider_info.base_url,
            "vector_hits": bundle.metadata.get("vector_hits", 0),
            "active_scope": (scope or FilingScope()).as_metadata(),
            "available_filings": available_filings,
        }
        if indexing_provider is None:
            debug.update(
                {
                    "embedding_provider": "none",
                    "embedding_model": "none",
                    "base_url": provider_info.base_url,
                }
            )
            return debug

        embed_info = indexing_provider.info
        debug.update(
            {
                "embedding_provider": embed_info.provider_name,
                "embedding_model": embed_info.embedding_model,
                "indexed_embedding_provider": embed_info.provider_name,
                "indexed_embedding_model": embed_info.embedding_model,
                "indexed_embedding_base_url": embed_info.base_url,
                "base_url": embed_info.base_url,
            }
        )
        return debug

    @staticmethod
    def _build_evidence_summary(plan: QueryPlan, bundle: EvidenceBundle) -> str:
        """Assemble a text summary of evidence for the chat model."""
        if plan.answer_shape == AnswerShape.COMPOSITE:
            return WorkbenchPipeline._build_composite_evidence_summary(bundle)

        parts: list[str] = []
        for chunk in bundle.section_chunks:
            parts.append(f"[{chunk.section_title}] {chunk.text}")
        for chunk in bundle.table_chunks:
            caption = f"Table: {chunk.caption}\n" if chunk.caption else ""
            parts.append(f"{caption}{chunk.raw_text}")
        for fact in bundle.facts:
            parts.append(
                f"Fact: {fact.label} = {fact.value * fact.scale:,.2f} {fact.unit} "
                f"(period ending {fact.period_end})"
            )
        return "\n\n".join(parts) if parts else "No evidence found."

    @staticmethod
    def _build_composite_evidence_summary(bundle: EvidenceBundle) -> str:
        """Assemble evidence with explicit narrative/numeric lanes."""
        parts: list[str] = []

        if bundle.section_chunks:
            parts.append("Narrative evidence:")
            for chunk in bundle.section_chunks[:3]:
                parts.append(f"- [{chunk.section_title}] {chunk.text}")

        numeric_parts: list[str] = []
        for chunk in bundle.table_chunks[:6]:
            caption = f"Table: {chunk.caption}\n" if chunk.caption else ""
            numeric_parts.append(f"{caption}{chunk.raw_text}")
        for fact in bundle.facts:
            numeric_parts.append(
                f"Fact: {fact.label} = {fact.value * fact.scale:,.2f} {fact.unit} "
                f"(period ending {fact.period_end})"
            )
        if numeric_parts:
            parts.append("Numeric evidence:")
            parts.extend(f"- {part}" for part in numeric_parts)

        return "\n".join(parts) if parts else "No evidence found."

    @staticmethod
    def _should_use_local_answer(
        plan: QueryPlan,
        bundle: EvidenceBundle,
        remote_text: str,
        local_answer: AnswerPayload,
    ) -> bool:
        """Fallback to the local answer when remote narration drops critical cues."""
        if not local_answer.answer_text.strip():
            return False

        cues = WorkbenchPipeline._answer_preservation_cues(plan)
        if not cues and plan.answer_shape != AnswerShape.COMPOSITE:
            return False

        remote_lower = remote_text.lower()
        local_lower = local_answer.answer_text.lower()

        if plan.query_language in (QueryLanguage.CHINESE, QueryLanguage.MIXED):
            remote_has_cjk = WorkbenchPipeline._contains_cjk(remote_text)
            local_has_cjk = WorkbenchPipeline._contains_cjk(local_answer.answer_text)
            if local_has_cjk and not remote_has_cjk:
                return True

        remote_score = sum(1 for cue in cues if cue in remote_lower)
        local_score = sum(1 for cue in cues if cue in local_lower)
        if local_score > remote_score:
            return True

        if plan.answer_shape == AnswerShape.COMPOSITE and bundle.section_chunks:
            narrative_cues = [
                cue
                for cue in cues
                if cue in {
                    "supply chain",
                    "供应链",
                    "risk factors",
                    "risk",
                    "风险",
                    "competition",
                    "竞争",
                    "logistics",
                    "物流",
                    "geopolitical",
                    "地缘政治",
                }
            ]
            if narrative_cues and not any(cue in remote_lower for cue in narrative_cues):
                return True

        return False

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    @staticmethod
    def _answer_preservation_cues(plan: QueryPlan) -> list[str]:
        question_surface = f"{plan.original_query.lower()} {plan.normalized_query.lower()}"
        cues: list[str] = []
        for cue in (
            "gross profit",
            "margin",
            "毛利润",
            "毛利率",
            "revenue",
            "总营收",
            "operating income",
            "quarter",
            "营业利润",
            "季度",
            "free cash flow",
            "capital expenditure",
            "自由现金流",
            "资本支出",
            "cash and cash equivalents",
            "现金及现金等价物",
            "supply chain",
            "供应链",
            "cost",
            "成本",
            "research and development",
            "研发",
            "trend",
            "趋势",
            "geopolitical",
            "地缘政治",
            "accounts payable",
        ):
            if cue in question_surface and cue not in cues:
                cues.append(cue)
        if plan.query_language in (QueryLanguage.CHINESE, QueryLanguage.MIXED):
            cues.append("结果")
        else:
            cues.append("result")
        return cues

    @staticmethod
    def _default_response_language(plan: QueryPlan) -> str | None:
        """Choose a response-language directive that follows the query language."""
        if plan.query_language in (QueryLanguage.CHINESE, QueryLanguage.MIXED):
            return response_language_directive("zh_CN")
        return None

    # ------------------------------------------------------------------
    # Scoped repositories
    # ------------------------------------------------------------------

    def _scoped_repositories(
        self,
        scope: FilingScope | None,
    ) -> tuple[InMemoryCorpusRepository, InMemoryFactsRepository]:
        if scope is None:
            return self._corpus_repo, self._facts_repo

        corpus_repo = InMemoryCorpusRepository()
        facts_repo = InMemoryFactsRepository()

        included_doc_ids = set()
        for filing in self._corpus_repo.list_filings():
            if not scope.matches(filing):
                continue
            included_doc_ids.add(filing.doc_id)
            corpus_repo.upsert_filing(filing)
            for chunk in self._corpus_repo.get_section_chunks(filing.doc_id):
                corpus_repo.upsert_section_chunk(chunk)
            for chunk in self._corpus_repo.get_table_chunks(filing.doc_id):
                corpus_repo.upsert_table_chunk(chunk)

        for fact in self._facts_repo.get_facts():
            if fact.doc_id in included_doc_ids:
                facts_repo.upsert_fact(fact)

        return corpus_repo, facts_repo


@lru_cache(maxsize=8)
def get_workbench_pipeline(
    provider_mode: ProviderMode = ProviderMode.LOCAL,
    processed_dir: str | None = None,
) -> WorkbenchPipeline:
    """Create a workbench pipeline with the given provider mode.

    Loads the processed corpus from the configured processed-data root. Raises
    :class:`~tesla_finrag.runtime.ProcessedCorpusError` when artifacts are
    missing or invalid.

    For both provider modes, the provider instance is constructed from
    :func:`~tesla_finrag.settings.get_settings`.
    """
    from tesla_finrag.runtime import load_processed_corpus

    corpus_repo, facts_repo, retrieval_store = load_processed_corpus(processed_dir)

    from tesla_finrag.provider import IndexingEmbeddingProvider

    indexing_provider = IndexingEmbeddingProvider.from_settings()

    if provider_mode == ProviderMode.LOCAL:
        from tesla_finrag.provider import OllamaProvider

        provider = OllamaProvider.from_settings()
    else:
        from tesla_finrag.provider import OpenAIProvider

        provider = OpenAIProvider.from_settings()

    return WorkbenchPipeline(
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        provider_mode=provider_mode,
        provider=provider,
        retrieval_store=retrieval_store,
        indexing_provider=indexing_provider,
    )
