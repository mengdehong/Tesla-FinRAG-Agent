"""Shared workbench pipeline for the Streamlit demo and evaluation runner.

This module wires the real planning -> retrieval -> answer pipeline over the
processed Tesla corpus loaded from ``data/processed/``.

The pipeline now supports two provider modes:
- ``local`` (default): deterministic template-based answers, no network calls.
- ``openai-compatible``: uses the OpenAI SDK for embeddings and grounded chat
  narration over the same processed corpus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from uuid import UUID

from tesla_finrag.answer import GroundedAnswerComposer
from tesla_finrag.models import (
    AnswerPayload,
    AnswerStatus,
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    FilingType,
    QueryPlan,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.planning import RuleBasedQueryPlanner
from tesla_finrag.retrieval import (
    HybridRetrievalService,
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
    InMemoryRetrievalStore,
)

logger = logging.getLogger(__name__)


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
        provider: object | None = None,
    ) -> None:
        self._corpus_repo = corpus_repo
        self._facts_repo = facts_repo
        self._planner = RuleBasedQueryPlanner()
        self._provider_mode = provider_mode
        self._provider = provider  # OpenAIProvider instance when remote
        self._vector_index_cache: dict[tuple[UUID, ...], InMemoryRetrievalStore] = {}

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

    def run(
        self,
        question: str,
        scope: FilingScope | None = None,
    ) -> tuple[QueryPlan, EvidenceBundle, AnswerPayload]:
        plan = self._planner.plan(question)
        corpus_repo, facts_repo = self._scoped_repositories(scope)

        if self._provider_mode == ProviderMode.OPENAI_COMPATIBLE:
            return self._run_remote(plan, corpus_repo, facts_repo, scope)
        return self._run_local(plan, corpus_repo, facts_repo, scope)

    # ------------------------------------------------------------------
    # Local (deterministic) path
    # ------------------------------------------------------------------

    def _run_local(
        self,
        plan: QueryPlan,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
        scope: FilingScope | None,
    ) -> tuple[QueryPlan, EvidenceBundle, AnswerPayload]:
        retrieval = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=None,
        )
        composer = GroundedAnswerComposer(corpus_repo=corpus_repo, facts_repo=facts_repo)

        bundle = retrieval.retrieve(plan)
        answer = composer.answer(plan, bundle)
        answer.retrieval_debug.update(
            {
                "provider_mode": ProviderMode.LOCAL.value,
                "embedding_provider": "none",
                "answer_provider": "template",
                "embedding_model": "none",
                "answer_model": "none",
                "chat_model": "none",
                "vector_hits": bundle.metadata.get("vector_hits", 0),
                "active_scope": (scope or FilingScope()).as_metadata(),
                "available_filings": len(corpus_repo.list_filings()),
            }
        )
        return plan, bundle, answer

    # ------------------------------------------------------------------
    # Remote (openai-compatible) path
    # ------------------------------------------------------------------

    def _run_remote(
        self,
        plan: QueryPlan,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
        scope: FilingScope | None,
    ) -> tuple[QueryPlan, EvidenceBundle, AnswerPayload]:
        from tesla_finrag.provider import OpenAIProvider, ProviderError

        if self._provider is None:
            raise ProviderError(
                "openai-compatible provider mode selected but no provider was configured. "
                "Set OPENAI_API_KEY in the environment."
            )

        provider: OpenAIProvider = self._provider  # type: ignore[assignment]

        # Build the vector index for the scoped corpus
        retrieval_store = self._build_remote_vector_index(corpus_repo, provider)

        # Create embed function for query-time
        def embed_fn(text: str) -> list[float]:
            vectors = provider.embed_texts([text])
            return vectors[0] if vectors else []

        retrieval = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=retrieval_store,
            embed_fn=embed_fn,
        )
        composer = GroundedAnswerComposer(corpus_repo=corpus_repo, facts_repo=facts_repo)

        bundle = retrieval.retrieve(plan)

        # Use local composer first to get citations, status, calc trace, confidence
        local_answer = composer.answer(plan, bundle)

        if local_answer.status != AnswerStatus.OK:
            local_answer.retrieval_debug.update(
                {
                    "provider_mode": ProviderMode.OPENAI_COMPATIBLE.value,
                    "embedding_provider": "openai-compatible",
                    "answer_provider": "template-guardrail",
                    **provider.info.as_dict(),
                    "vector_hits": bundle.metadata.get("vector_hits", 0),
                    "active_scope": (scope or FilingScope()).as_metadata(),
                    "available_filings": len(corpus_repo.list_filings()),
                }
            )
            return plan, bundle, local_answer

        # Now narrate the answer text using the remote chat model
        evidence_summary = self._build_evidence_summary(bundle)
        try:
            remote_text = provider.generate_grounded_answer(
                question=plan.original_query,
                evidence=evidence_summary,
                calculation_trace=local_answer.calculation_trace or None,
            )
        except ProviderError:
            logger.exception("Remote chat narration failed")
            raise

        # Replace the template answer text with the remote-narrated text
        answer = AnswerPayload(
            plan_id=local_answer.plan_id,
            status=local_answer.status,
            answer_text=remote_text,
            citations=local_answer.citations,
            calculation_trace=local_answer.calculation_trace,
            retrieval_debug=local_answer.retrieval_debug,
            confidence=local_answer.confidence,
        )
        answer.retrieval_debug.update(
            {
                "provider_mode": ProviderMode.OPENAI_COMPATIBLE.value,
                "embedding_provider": "openai-compatible",
                "answer_provider": "openai-compatible",
                **provider.info.as_dict(),
                "vector_hits": bundle.metadata.get("vector_hits", 0),
                "active_scope": (scope or FilingScope()).as_metadata(),
                "available_filings": len(corpus_repo.list_filings()),
            }
        )
        return plan, bundle, answer

    def _build_remote_vector_index(
        self,
        corpus_repo: InMemoryCorpusRepository,
        provider: object,
    ) -> InMemoryRetrievalStore:
        """Embed all corpus chunks and build an in-memory vector index."""
        from tesla_finrag.provider import OpenAIProvider

        assert isinstance(provider, OpenAIProvider)

        cache_key = tuple(sorted((filing.doc_id for filing in corpus_repo.list_filings()), key=str))
        cached_store = self._vector_index_cache.get(cache_key)
        if cached_store is not None:
            return cached_store

        store = InMemoryRetrievalStore()
        all_chunks: list[SectionChunk | TableChunk] = []
        all_chunks.extend(corpus_repo.all_section_chunks())
        all_chunks.extend(corpus_repo.all_table_chunks())

        if not all_chunks:
            return store

        # Batch embed all chunk texts
        texts = [_chunk_text(c) for c in all_chunks]
        embeddings = provider.embed_texts(texts)

        for chunk, embedding in zip(all_chunks, embeddings):
            if isinstance(chunk, SectionChunk):
                store.index_section_chunk(chunk, embedding)
            else:
                store.index_table_chunk(chunk, embedding)

        self._vector_index_cache[cache_key] = store
        logger.info("Built remote vector index with %d chunks", len(all_chunks))
        return store

    @staticmethod
    def _build_evidence_summary(bundle: EvidenceBundle) -> str:
        """Assemble a text summary of evidence for the chat model."""
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


@lru_cache(maxsize=1)
def get_workbench_pipeline(
    provider_mode: ProviderMode = ProviderMode.LOCAL,
) -> WorkbenchPipeline:
    """Create a workbench pipeline with the given provider mode.

    Loads the processed corpus from ``data/processed/``.  Raises
    :class:`~tesla_finrag.runtime.ProcessedCorpusError` when artifacts are
    missing or invalid.

    For ``openai-compatible`` mode, the provider is constructed from
    :func:`~tesla_finrag.settings.get_settings`.
    """
    from tesla_finrag.runtime import load_processed_corpus

    corpus_repo, facts_repo = load_processed_corpus()

    provider = None
    if provider_mode == ProviderMode.OPENAI_COMPATIBLE:
        from tesla_finrag.provider import OpenAIProvider

        provider = OpenAIProvider.from_settings()

    return WorkbenchPipeline(
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        provider_mode=provider_mode,
        provider=provider,
    )
