"""Tests for LanceDB-backed hybrid retrieval integration.

Covers:
- Scoped hybrid search through HybridRetrievalService with a LanceDBRetrievalStore
- Provider diagnostics reflect the indexed embedding backend
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from tesla_finrag.ingestion.index_segmentation import ChunkSegment
from tesla_finrag.models import QueryLanguage, QueryPlan, SectionChunk, SubQuery, TableChunk
from tesla_finrag.retrieval import (
    HybridRetrievalService,
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
)
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore
from tesla_finrag.retrieval.lexical import _tokenize

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DOC_ID = UUID("00000000-0000-0000-0000-000000000001")
_EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


@pytest.fixture()
def populated_store(tmp_path: Path) -> LanceDBRetrievalStore:
    """A LanceDB store pre-populated with one section and one table chunk."""
    db_dir = tmp_path / "lancedb"
    db_dir.mkdir()
    store = LanceDBRetrievalStore(db_dir)

    section = SectionChunk(
        doc_id=_DOC_ID,
        section_title="Management Discussion",
        text="Tesla revenue grew 20% year-over-year.",
        token_count=7,
    )
    table = TableChunk(
        doc_id=_DOC_ID,
        section_title="Revenue Breakdown",
        caption="Revenue by segment",
        headers=["Segment", "Revenue"],
        rows=[["Automotive", "21,268"]],
        raw_text="Segment | Revenue\nAutomotive | 21,268",
    )
    store.index_section_chunk(section, _EMBEDDING)
    store.index_table_chunk(table, [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
    return store


@pytest.fixture()
def corpus_repo() -> InMemoryCorpusRepository:
    """Minimal corpus repo with chunks matching the store."""
    from tesla_finrag.models import FilingDocument, FilingType

    repo = InMemoryCorpusRepository()
    filing = FilingDocument(
        doc_id=_DOC_ID,
        filing_type=FilingType.QUARTERLY,
        period_end=date(2023, 6, 30),
        fiscal_year=2023,
        fiscal_quarter=2,
        accession_number="0000950170-2023-06",
        filed_at=date(2023, 7, 15),
        source_path="data/raw/Tesla_2023_Q2_10-Q.pdf",
    )
    repo.upsert_filing(filing)

    section = SectionChunk(
        doc_id=_DOC_ID,
        section_title="Management Discussion",
        text="Tesla revenue grew 20% year-over-year.",
        token_count=7,
    )
    table = TableChunk(
        doc_id=_DOC_ID,
        section_title="Revenue Breakdown",
        caption="Revenue by segment",
        headers=["Segment", "Revenue"],
        rows=[["Automotive", "21,268"]],
        raw_text="Segment | Revenue\nAutomotive | 21,268",
    )
    repo.upsert_section_chunk(section)
    repo.upsert_table_chunk(table)
    return repo


@pytest.fixture()
def facts_repo() -> InMemoryFactsRepository:
    return InMemoryFactsRepository()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLanceDBHybridRetrieval:
    def test_hybrid_service_uses_lancedb_store(
        self,
        populated_store: LanceDBRetrievalStore,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        """HybridRetrievalService should work with a LanceDB retrieval store."""

        def embed_fn(text: str) -> list[float]:
            return _EMBEDDING

        service = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=populated_store,
            embed_fn=embed_fn,
        )

        plan = QueryPlan(original_query="What was Tesla's revenue?", sub_questions=["revenue"])
        bundle = service.retrieve(plan)

        # Should have retrieved at least some chunks
        total = len(bundle.section_chunks) + len(bundle.table_chunks)
        assert total > 0

    def test_lancedb_store_metadata_reflects_backend(
        self,
        populated_store: LanceDBRetrievalStore,
    ) -> None:
        """Metadata should reflect the indexing backend information."""
        populated_store.save_metadata(
            {
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "chunk_count": 2,
            }
        )
        meta = populated_store.load_metadata()
        assert meta is not None
        assert meta["embedding_model"] == "nomic-embed-text"
        assert meta["chunk_count"] == 2

    def test_hybrid_service_dedupes_segmented_vector_rows(
        self,
        tmp_path: Path,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        section = corpus_repo.get_section_chunks(_DOC_ID)[0]
        store.index_chunk_segments(
            section,
            [
                ChunkSegment(text="segment one", segment_index=0, segment_count=2),
                ChunkSegment(text="segment two", segment_index=1, segment_count=2),
            ],
            [_EMBEDDING, _EMBEDDING],
        )

        service = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=store,
            embed_fn=lambda _: _EMBEDDING,
        )

        bundle = service.retrieve(QueryPlan(original_query="revenue"))
        assert len(bundle.section_chunks) == 1
        assert bundle.section_chunks[0].chunk_id == section.chunk_id

    def test_tokenizer_supports_cjk_queries(self) -> None:
        assert "供应链风险因素" in _tokenize("供应链风险因素")
        assert "供应" in _tokenize("供应链风险因素")
        assert "fy2023" in _tokenize("比较特斯拉FY2023的营收")

    def test_hybrid_service_uses_normalized_query_text_for_chinese_question(
        self,
        populated_store: LanceDBRetrievalStore,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        service = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=populated_store,
            embed_fn=lambda _: _EMBEDDING,
        )

        plan = QueryPlan(
            original_query="特斯拉 2023 年的营收是多少？",
            normalized_query="tesla revenue FY2023",
            query_language=QueryLanguage.CHINESE,
            sub_questions=["特斯拉 2023 年的营收是多少？"],
        )
        bundle = service.retrieve(plan)

        total = len(bundle.section_chunks) + len(bundle.table_chunks)
        assert total > 0
        assert bundle.metadata["original_query_text"] == "特斯拉 2023 年的营收是多少？"
        assert bundle.metadata["normalized_query_text"] == "tesla revenue FY2023"

    def test_hybrid_service_uses_sub_query_search_text_in_per_period_mode(
        self,
        populated_store: LanceDBRetrievalStore,
        corpus_repo: InMemoryCorpusRepository,
        facts_repo: InMemoryFactsRepository,
    ) -> None:
        service = HybridRetrievalService(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            retrieval_store=populated_store,
            embed_fn=lambda _: _EMBEDDING,
        )

        plan = QueryPlan(
            original_query="比较 2023Q2 的营收表现",
            normalized_query="revenue Q2 2023",
            query_language=QueryLanguage.CHINESE,
            sub_questions=["比较 2023Q2 的营收表现"],
            sub_queries=[
                SubQuery(
                    text="比较 2023Q2 的营收表现",
                    search_text="revenue Q2 2023",
                    target_period=date(2023, 6, 30),
                )
            ],
        )
        bundle = service.retrieve(plan)
        per_period = next(iter(bundle.metadata["per_period"].values()))
        assert per_period["query_text"] == "revenue Q2 2023"
        assert per_period["original_sub_query_text"] == "比较 2023Q2 的营收表现"
