"""Smoke tests for repository and service interface contracts.

These tests verify that:
- Concrete test-double implementations are possible (no ABC issues).
- The interfaces accept and return the expected typed objects.
- Abstract methods cannot be skipped.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

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
from tesla_finrag.repositories import (
    CorpusRepository,
    EvidenceRepository,
    FactsRepository,
    QueryPlanRepository,
    RetrievalStore,
)
from tesla_finrag.services import (
    AnswerService,
    CalculationService,
    IngestionService,
    QueryPlanningService,
    RetrievalService,
)

# ---------------------------------------------------------------------------
# Test-double (in-memory) implementations
# ---------------------------------------------------------------------------


class InMemoryCorpusRepository(CorpusRepository):
    def __init__(self) -> None:
        self._filings: dict[UUID, FilingDocument] = {}
        self._sections: dict[UUID, list[SectionChunk]] = {}
        self._tables: dict[UUID, list[TableChunk]] = {}

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
        results = list(self._filings.values())
        if period_end_after:
            results = [f for f in results if f.period_end >= period_end_after]
        if period_end_before:
            results = [f for f in results if f.period_end <= period_end_before]
        return results

    def upsert_section_chunk(self, chunk: SectionChunk) -> None:
        self._sections.setdefault(chunk.doc_id, []).append(chunk)

    def get_section_chunks(self, doc_id: UUID) -> list[SectionChunk]:
        return self._sections.get(doc_id, [])

    def upsert_table_chunk(self, chunk: TableChunk) -> None:
        self._tables.setdefault(chunk.doc_id, []).append(chunk)

    def get_table_chunks(self, doc_id: UUID) -> list[TableChunk]:
        return self._tables.get(doc_id, [])


class InMemoryFactsRepository(FactsRepository):
    def __init__(self) -> None:
        self._facts: list[FactRecord] = []

    def upsert_fact(self, fact: FactRecord) -> None:
        self._facts.append(fact)

    def get_facts(
        self,
        *,
        doc_id: UUID | None = None,
        concept: str | None = None,
        period_end: date | None = None,
    ) -> list[FactRecord]:
        results = self._facts
        if doc_id:
            results = [f for f in results if f.doc_id == doc_id]
        if concept:
            results = [f for f in results if f.concept == concept]
        if period_end:
            results = [f for f in results if f.period_end == period_end]
        return results

    def list_concepts(self, doc_id: UUID | None = None) -> list[str]:
        subset = self._facts if doc_id is None else [f for f in self._facts if f.doc_id == doc_id]
        return list({f.concept for f in subset})


class InMemoryRetrievalStore(RetrievalStore):
    def __init__(self) -> None:
        self._index: list[tuple[SectionChunk | TableChunk, list[float]]] = []

    def index_section_chunk(self, chunk: SectionChunk, embedding: list[float]) -> None:
        self._index.append((chunk, embedding))

    def index_table_chunk(self, chunk: TableChunk, embedding: list[float]) -> None:
        self._index.append((chunk, embedding))

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[tuple[SectionChunk | TableChunk, float]]:
        # Naive dot-product similarity for smoke testing
        def dot(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b))

        scored = [(chunk, dot(query_embedding, emb)) for chunk, emb in self._index]
        if doc_ids:
            scored = [(c, s) for c, s in scored if c.doc_id in doc_ids]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class InMemoryEvidenceRepository(EvidenceRepository):
    def __init__(self) -> None:
        self._bundles: dict[UUID, EvidenceBundle] = {}

    def save_bundle(self, bundle: EvidenceBundle) -> None:
        self._bundles[bundle.bundle_id] = bundle

    def get_bundle(self, bundle_id: UUID) -> EvidenceBundle | None:
        return self._bundles.get(bundle_id)

    def get_bundles_for_plan(self, plan_id: UUID) -> list[EvidenceBundle]:
        return [b for b in self._bundles.values() if b.plan_id == plan_id]


class InMemoryQueryPlanRepository(QueryPlanRepository):
    def __init__(self) -> None:
        self._plans: dict[UUID, QueryPlan] = {}

    def save_plan(self, plan: QueryPlan) -> None:
        self._plans[plan.plan_id] = plan

    def get_plan(self, plan_id: UUID) -> QueryPlan | None:
        return self._plans.get(plan_id)


# ---------------------------------------------------------------------------
# Service test doubles
# ---------------------------------------------------------------------------


class EchoIngestionService(IngestionService):
    """Records the last ingested filing without doing real work."""

    def __init__(self) -> None:
        self.ingested: list[FilingDocument] = []

    def ingest(self, filing: FilingDocument) -> None:
        self.ingested.append(filing)

    def ingest_batch(self, filings: list[FilingDocument]) -> None:
        for f in filings:
            self.ingest(f)


class FixedQueryPlanningService(QueryPlanningService):
    def plan(self, question: str) -> QueryPlan:
        return QueryPlan(original_query=question)


class FixedRetrievalService(RetrievalService):
    def retrieve(self, plan: QueryPlan) -> EvidenceBundle:
        return EvidenceBundle(plan_id=plan.plan_id)


class ConstantCalculationService(CalculationService):
    def calculate(self, expression: str, facts: list[FactRecord]) -> tuple[float, list[str]]:
        return 0.0, [f"evaluated: {expression}"]


class FixedAnswerService(AnswerService):
    def answer(self, plan: QueryPlan, bundle: EvidenceBundle) -> AnswerPayload:
        return AnswerPayload(
            plan_id=plan.plan_id,
            status=AnswerStatus.OK,
            answer_text="Fixed answer.",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def filing() -> FilingDocument:
    return FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2023, 12, 31),
        fiscal_year=2023,
        accession_number="0000000000-24-000001",
        filed_at=date(2024, 1, 29),
        source_path="data/raw/TSLA_10-K_2023.pdf",
    )


# ---------------------------------------------------------------------------
# CorpusRepository smoke tests
# ---------------------------------------------------------------------------


class TestInMemoryCorpusRepository:
    def test_upsert_and_get_filing(self, filing: FilingDocument) -> None:
        repo = InMemoryCorpusRepository()
        repo.upsert_filing(filing)
        assert repo.get_filing(filing.doc_id) == filing

    def test_get_missing_filing_returns_none(self) -> None:
        repo = InMemoryCorpusRepository()
        assert repo.get_filing(uuid4()) is None

    def test_list_filings_returns_all(self, filing: FilingDocument) -> None:
        repo = InMemoryCorpusRepository()
        repo.upsert_filing(filing)
        assert len(repo.list_filings()) == 1

    def test_list_filings_date_filter(self, filing: FilingDocument) -> None:
        repo = InMemoryCorpusRepository()
        repo.upsert_filing(filing)
        # Filter that excludes the filing
        result = repo.list_filings(period_end_after=date(2024, 1, 1))
        assert result == []

    def test_section_chunk_round_trip(self, filing: FilingDocument) -> None:
        repo = InMemoryCorpusRepository()
        chunk = SectionChunk(
            doc_id=filing.doc_id,
            section_title="MD&A",
            text="Revenue grew.",
            token_count=3,
        )
        repo.upsert_section_chunk(chunk)
        assert repo.get_section_chunks(filing.doc_id) == [chunk]

    def test_table_chunk_round_trip(self, filing: FilingDocument) -> None:
        repo = InMemoryCorpusRepository()
        chunk = TableChunk(
            doc_id=filing.doc_id,
            section_title="Income Statement",
            raw_text="Revenue | 96773",
        )
        repo.upsert_table_chunk(chunk)
        assert repo.get_table_chunks(filing.doc_id) == [chunk]


# ---------------------------------------------------------------------------
# FactsRepository smoke tests
# ---------------------------------------------------------------------------


class TestInMemoryFactsRepository:
    def test_upsert_and_get_by_concept(self, filing: FilingDocument) -> None:
        repo = InMemoryFactsRepository()
        fact = FactRecord(
            doc_id=filing.doc_id,
            concept="us-gaap:Revenues",
            label="Revenue",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        repo.upsert_fact(fact)
        results = repo.get_facts(concept="us-gaap:Revenues")
        assert results == [fact]

    def test_list_concepts(self, filing: FilingDocument) -> None:
        repo = InMemoryFactsRepository()
        for concept in ("us-gaap:Revenues", "us-gaap:NetIncomeLoss"):
            repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept=concept,
                    label=concept,
                    value=1.0,
                    unit="USD",
                    period_end=date(2023, 12, 31),
                )
            )
        assert set(repo.list_concepts()) == {"us-gaap:Revenues", "us-gaap:NetIncomeLoss"}


# ---------------------------------------------------------------------------
# RetrievalStore smoke tests
# ---------------------------------------------------------------------------


class TestInMemoryRetrievalStore:
    def test_search_returns_ranked_results(self, filing: FilingDocument) -> None:
        store = InMemoryRetrievalStore()
        chunk_a = SectionChunk(
            doc_id=filing.doc_id,
            section_title="Revenue",
            text="Revenue was high.",
            token_count=3,
        )
        chunk_b = SectionChunk(
            doc_id=filing.doc_id,
            section_title="Costs",
            text="Costs were rising.",
            token_count=3,
        )
        store.index_section_chunk(chunk_a, [1.0, 0.0])
        store.index_section_chunk(chunk_b, [0.0, 1.0])
        results = store.search([1.0, 0.0], top_k=2)
        assert results[0][0] == chunk_a

    def test_search_respects_top_k(self, filing: FilingDocument) -> None:
        store = InMemoryRetrievalStore()
        for i in range(5):
            store.index_section_chunk(
                SectionChunk(
                    doc_id=filing.doc_id,
                    section_title=f"Sec {i}",
                    text="text",
                    token_count=1,
                ),
                [float(i), 0.0],
            )
        results = store.search([1.0, 0.0], top_k=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# EvidenceRepository smoke tests
# ---------------------------------------------------------------------------


class TestInMemoryEvidenceRepository:
    def test_save_and_retrieve_bundle(self) -> None:
        repo = InMemoryEvidenceRepository()
        plan = QueryPlan(original_query="Q")
        bundle = EvidenceBundle(plan_id=plan.plan_id)
        repo.save_bundle(bundle)
        assert repo.get_bundle(bundle.bundle_id) == bundle

    def test_get_bundles_for_plan(self) -> None:
        repo = InMemoryEvidenceRepository()
        plan = QueryPlan(original_query="Q")
        b1 = EvidenceBundle(plan_id=plan.plan_id)
        b2 = EvidenceBundle(plan_id=plan.plan_id)
        other = EvidenceBundle(plan_id=uuid4())
        for b in (b1, b2, other):
            repo.save_bundle(b)
        result = repo.get_bundles_for_plan(plan.plan_id)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Service interface smoke tests
# ---------------------------------------------------------------------------


class TestServiceInterfaces:
    def test_ingestion_service(self, filing: FilingDocument) -> None:
        svc = EchoIngestionService()
        svc.ingest(filing)
        assert svc.ingested == [filing]

    def test_ingest_batch(self, filing: FilingDocument) -> None:
        svc = EchoIngestionService()
        svc.ingest_batch([filing, filing])
        assert len(svc.ingested) == 2

    def test_query_planning_service(self) -> None:
        svc = FixedQueryPlanningService()
        plan = svc.plan("What was revenue in 2023?")
        assert isinstance(plan, QueryPlan)
        assert plan.original_query == "What was revenue in 2023?"

    def test_retrieval_service(self) -> None:
        svc = FixedRetrievalService()
        plan = QueryPlan(original_query="Revenue?")
        bundle = svc.retrieve(plan)
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.plan_id == plan.plan_id

    def test_calculation_service(self) -> None:
        svc = ConstantCalculationService()
        result, trace = svc.calculate("Revenue / COGS", [])
        assert isinstance(result, float)
        assert len(trace) == 1

    def test_answer_service(self) -> None:
        svc = FixedAnswerService()
        plan = QueryPlan(original_query="Revenue?")
        bundle = EvidenceBundle(plan_id=plan.plan_id)
        payload = svc.answer(plan, bundle)
        assert isinstance(payload, AnswerPayload)
        assert payload.status == AnswerStatus.OK


# ---------------------------------------------------------------------------
# Abstract method enforcement
# ---------------------------------------------------------------------------


class TestAbstractMethods:
    def test_corpus_repo_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            CorpusRepository()  # type: ignore[abstract]

    def test_facts_repo_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            FactsRepository()  # type: ignore[abstract]

    def test_retrieval_store_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            RetrievalStore()  # type: ignore[abstract]

    def test_ingestion_service_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            IngestionService()  # type: ignore[abstract]

    def test_answer_service_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            AnswerService()  # type: ignore[abstract]
