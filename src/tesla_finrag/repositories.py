"""Abstract repository interfaces for the Tesla FinRAG storage layer.

Repository classes define the storage contract without prescribing the
backend.  Concrete implementations (LanceDB, in-memory, file-backed) are
provided by later changes and registered via dependency injection or
factory functions.

All concrete repositories MUST inherit from the appropriate abstract base
and implement every abstract method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from uuid import UUID

from tesla_finrag.models import (
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    QueryPlan,
    SectionChunk,
    TableChunk,
)

# ---------------------------------------------------------------------------
# Corpus repository — filing metadata and raw chunks
# ---------------------------------------------------------------------------


class CorpusRepository(ABC):
    """Persist and retrieve filing metadata and corpus chunks."""

    # ── FilingDocument ───────────────────────────────────────────────────────

    @abstractmethod
    def upsert_filing(self, filing: FilingDocument) -> None:
        """Insert or update a filing metadata record."""

    @abstractmethod
    def get_filing(self, doc_id: UUID) -> FilingDocument | None:
        """Return the filing with the given ID, or None if not found."""

    @abstractmethod
    def list_filings(
        self,
        *,
        period_end_after: date | None = None,
        period_end_before: date | None = None,
    ) -> list[FilingDocument]:
        """Return filings optionally filtered by period date range."""

    # ── SectionChunk ─────────────────────────────────────────────────────────

    @abstractmethod
    def upsert_section_chunk(self, chunk: SectionChunk) -> None:
        """Insert or update a section chunk record."""

    @abstractmethod
    def get_section_chunks(self, doc_id: UUID) -> list[SectionChunk]:
        """Return all section chunks belonging to a filing."""

    # ── TableChunk ───────────────────────────────────────────────────────────

    @abstractmethod
    def upsert_table_chunk(self, chunk: TableChunk) -> None:
        """Insert or update a table chunk record."""

    @abstractmethod
    def get_table_chunks(self, doc_id: UUID) -> list[TableChunk]:
        """Return all table chunks belonging to a filing."""


# ---------------------------------------------------------------------------
# Facts repository — normalised XBRL / parsed financial facts
# ---------------------------------------------------------------------------


class FactsRepository(ABC):
    """Persist and query normalised financial fact records."""

    @abstractmethod
    def upsert_fact(self, fact: FactRecord) -> None:
        """Insert or update a financial fact."""

    @abstractmethod
    def get_facts(
        self,
        *,
        doc_id: UUID | None = None,
        concept: str | None = None,
        period_end: date | None = None,
    ) -> list[FactRecord]:
        """Return facts filtered by optional doc_id, concept, and period."""

    @abstractmethod
    def list_concepts(self, doc_id: UUID | None = None) -> list[str]:
        """Return the distinct XBRL concept names present in the store."""


# ---------------------------------------------------------------------------
# Retrieval store — vector index for semantic search
# ---------------------------------------------------------------------------


class RetrievalStore(ABC):
    """Vector-index backed store for semantic chunk retrieval."""

    @abstractmethod
    def index_section_chunk(self, chunk: SectionChunk, embedding: list[float]) -> None:
        """Add or update a section chunk with its pre-computed embedding."""

    @abstractmethod
    def index_table_chunk(self, chunk: TableChunk, embedding: list[float]) -> None:
        """Add or update a table chunk with its pre-computed embedding."""

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[tuple[SectionChunk | TableChunk, float]]:
        """Return the top-k chunks most similar to ``query_embedding``.

        Args:
            query_embedding: Dense vector for the search query.
            top_k: Maximum number of results to return.
            doc_ids: If given, restrict search to these filing documents.

        Returns:
            List of ``(chunk, score)`` pairs sorted by descending score.
        """


# ---------------------------------------------------------------------------
# Evidence repository — persisted evidence bundles (optional caching)
# ---------------------------------------------------------------------------


class EvidenceRepository(ABC):
    """Optional cache for assembled evidence bundles."""

    @abstractmethod
    def save_bundle(self, bundle: EvidenceBundle) -> None:
        """Persist an evidence bundle for later inspection or caching."""

    @abstractmethod
    def get_bundle(self, bundle_id: UUID) -> EvidenceBundle | None:
        """Return a previously saved bundle, or None."""

    @abstractmethod
    def get_bundles_for_plan(self, plan_id: UUID) -> list[EvidenceBundle]:
        """Return all bundles associated with a query plan."""


# ---------------------------------------------------------------------------
# Query plan repository — persisted query plans
# ---------------------------------------------------------------------------


class QueryPlanRepository(ABC):
    """Persist and retrieve structured query plans."""

    @abstractmethod
    def save_plan(self, plan: QueryPlan) -> None:
        """Persist a query plan."""

    @abstractmethod
    def get_plan(self, plan_id: UUID) -> QueryPlan | None:
        """Return a previously saved plan, or None."""
