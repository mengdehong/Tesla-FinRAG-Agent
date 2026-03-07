"""In-memory implementations of all repository interfaces.

These are suitable for testing and development.  A production deployment
would swap in LanceDB or another persistent backend.
"""

from __future__ import annotations

import math
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
from tesla_finrag.repositories import (
    CorpusRepository,
    EvidenceRepository,
    FactsRepository,
    QueryPlanRepository,
    RetrievalStore,
)

# ---------------------------------------------------------------------------
# Corpus repository
# ---------------------------------------------------------------------------


class InMemoryCorpusRepository(CorpusRepository):
    """In-memory implementation of :class:`CorpusRepository`."""

    def __init__(self) -> None:
        self._filings: dict[UUID, FilingDocument] = {}
        self._section_chunks: dict[UUID, list[SectionChunk]] = {}
        self._table_chunks: dict[UUID, list[TableChunk]] = {}

    def upsert_filing(self, filing: FilingDocument) -> None:
        self._filings[filing.doc_id] = filing

    def get_filing(self, doc_id: UUID) -> FilingDocument | None:
        return self._filings.get(doc_id)

    def list_filings(
        self,
        *,
        period_end_after: date | None = None,
        period_end_before: date | None = None,
    ) -> list[FilingDocument]:
        result = list(self._filings.values())
        if period_end_after is not None:
            result = [f for f in result if f.period_end >= period_end_after]
        if period_end_before is not None:
            result = [f for f in result if f.period_end <= period_end_before]
        return result

    def upsert_section_chunk(self, chunk: SectionChunk) -> None:
        chunks = self._section_chunks.setdefault(chunk.doc_id, [])
        # Replace existing chunk with same ID
        chunks[:] = [c for c in chunks if c.chunk_id != chunk.chunk_id]
        chunks.append(chunk)

    def get_section_chunks(self, doc_id: UUID) -> list[SectionChunk]:
        return list(self._section_chunks.get(doc_id, []))

    def upsert_table_chunk(self, chunk: TableChunk) -> None:
        chunks = self._table_chunks.setdefault(chunk.doc_id, [])
        chunks[:] = [c for c in chunks if c.chunk_id != chunk.chunk_id]
        chunks.append(chunk)

    def get_table_chunks(self, doc_id: UUID) -> list[TableChunk]:
        return list(self._table_chunks.get(doc_id, []))

    # -- Convenience methods for retrieval ------------------------------------

    def all_section_chunks(self) -> list[SectionChunk]:
        """Return every section chunk across all filings."""
        return [c for chunks in self._section_chunks.values() for c in chunks]

    def all_table_chunks(self) -> list[TableChunk]:
        """Return every table chunk across all filings."""
        return [c for chunks in self._table_chunks.values() for c in chunks]


# ---------------------------------------------------------------------------
# Facts repository
# ---------------------------------------------------------------------------


class InMemoryFactsRepository(FactsRepository):
    """In-memory implementation of :class:`FactsRepository`."""

    def __init__(self) -> None:
        self._facts: list[FactRecord] = []

    def upsert_fact(self, fact: FactRecord) -> None:
        self._facts = [f for f in self._facts if f.fact_id != fact.fact_id]
        self._facts.append(fact)

    def get_facts(
        self,
        *,
        doc_id: UUID | None = None,
        concept: str | None = None,
        period_end: date | None = None,
    ) -> list[FactRecord]:
        result = list(self._facts)
        if doc_id is not None:
            result = [f for f in result if f.doc_id == doc_id]
        if concept is not None:
            result = [f for f in result if f.concept == concept]
        if period_end is not None:
            result = [f for f in result if f.period_end == period_end]
        return result

    def list_concepts(self, doc_id: UUID | None = None) -> list[str]:
        facts = self._facts
        if doc_id is not None:
            facts = [f for f in facts if f.doc_id == doc_id]
        return sorted({f.concept for f in facts})


# ---------------------------------------------------------------------------
# Retrieval store (vector index)
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryRetrievalStore(RetrievalStore):
    """In-memory vector store using brute-force cosine similarity.

    Suitable for small corpora during development and testing.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[SectionChunk | TableChunk, list[float]]] = []

    def index_section_chunk(self, chunk: SectionChunk, embedding: list[float]) -> None:
        # Remove existing entry with same chunk_id
        self._entries = [(c, e) for c, e in self._entries if c.chunk_id != chunk.chunk_id]
        self._entries.append((chunk, embedding))

    def index_table_chunk(self, chunk: TableChunk, embedding: list[float]) -> None:
        self._entries = [(c, e) for c, e in self._entries if c.chunk_id != chunk.chunk_id]
        self._entries.append((chunk, embedding))

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[tuple[SectionChunk | TableChunk, float]]:
        candidates = self._entries
        if doc_ids is not None:
            id_set = set(doc_ids)
            candidates = [(c, e) for c, e in candidates if c.doc_id in id_set]

        scored = [(chunk, _cosine_similarity(query_embedding, emb)) for chunk, emb in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# ---------------------------------------------------------------------------
# Evidence repository
# ---------------------------------------------------------------------------


class InMemoryEvidenceRepository(EvidenceRepository):
    """In-memory implementation of :class:`EvidenceRepository`."""

    def __init__(self) -> None:
        self._bundles: dict[UUID, EvidenceBundle] = {}

    def save_bundle(self, bundle: EvidenceBundle) -> None:
        self._bundles[bundle.bundle_id] = bundle

    def get_bundle(self, bundle_id: UUID) -> EvidenceBundle | None:
        return self._bundles.get(bundle_id)

    def get_bundles_for_plan(self, plan_id: UUID) -> list[EvidenceBundle]:
        return [b for b in self._bundles.values() if b.plan_id == plan_id]


# ---------------------------------------------------------------------------
# Query plan repository
# ---------------------------------------------------------------------------


class InMemoryQueryPlanRepository(QueryPlanRepository):
    """In-memory implementation of :class:`QueryPlanRepository`."""

    def __init__(self) -> None:
        self._plans: dict[UUID, QueryPlan] = {}

    def save_plan(self, plan: QueryPlan) -> None:
        self._plans[plan.plan_id] = plan

    def get_plan(self, plan_id: UUID) -> QueryPlan | None:
        return self._plans.get(plan_id)
