"""Tests for LanceDB-backed hybrid retrieval integration.

Covers:
- Scoped hybrid search through HybridRetrievalService with a LanceDBRetrievalStore
- Provider diagnostics reflect the indexed embedding backend
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from tesla_finrag.models import SectionChunk, TableChunk
from tesla_finrag.retrieval import HybridRetrievalService, InMemoryCorpusRepository, InMemoryFactsRepository
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore


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
    from datetime import date

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

        from tesla_finrag.models import QueryPlan

        plan = QueryPlan(original_query="What was Tesla's revenue?", sub_queries=["revenue"])
        bundle = service.retrieve(plan)

        # Should have retrieved at least some chunks
        total = len(bundle.section_chunks) + len(bundle.table_chunks)
        assert total > 0

    def test_lancedb_store_metadata_reflects_backend(
        self,
        populated_store: LanceDBRetrievalStore,
    ) -> None:
        """Metadata should reflect the indexing backend information."""
        populated_store.save_metadata({
            "embedding_model": "nomic-embed-text",
            "embedding_base_url": "http://localhost:11434/v1",
            "chunk_count": 2,
        })
        meta = populated_store.load_metadata()
        assert meta is not None
        assert meta["embedding_model"] == "nomic-embed-text"
        assert meta["chunk_count"] == 2
