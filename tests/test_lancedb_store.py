"""Tests for the LanceDB-backed RetrievalStore implementation.

Covers:
- Upsert and search for section/table chunks
- Filtered search by doc_id
- Metadata persistence and reload
- Startup failure when LanceDB dir is missing
- IncompatibleIndexError for embedding model mismatch
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from tesla_finrag.ingestion.index_segmentation import ChunkSegment
from tesla_finrag.models import SectionChunk, TableChunk
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore
from tesla_finrag.runtime import MissingProcessedArtifactError, validate_processed_dir

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def lancedb_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for LanceDB storage."""
    db_dir = tmp_path / "lancedb"
    db_dir.mkdir()
    return db_dir


@pytest.fixture()
def sample_section_chunk() -> SectionChunk:
    return SectionChunk(
        doc_id=UUID("00000000-0000-0000-0000-000000000001"),
        section_title="Risk Factors",
        text="Tesla faces supply chain risks.",
        token_count=6,
    )


@pytest.fixture()
def sample_table_chunk() -> TableChunk:
    return TableChunk(
        doc_id=UUID("00000000-0000-0000-0000-000000000001"),
        section_title="Revenue Breakdown",
        caption="Revenue by segment",
        headers=["Segment", "Revenue"],
        rows=[["Automotive", "21,268"]],
        raw_text="Automotive | 21,268",
    )


@pytest.fixture()
def sample_embedding() -> list[float]:
    """A deterministic 8-dimensional embedding for testing."""
    return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestLanceDBRetrievalStore:
    def test_index_and_search_section_chunk(
        self,
        lancedb_dir: Path,
        sample_section_chunk: SectionChunk,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        store.index_section_chunk(sample_section_chunk, sample_embedding)

        results = store.search(sample_embedding, top_k=5)
        assert len(results) == 1
        chunk, score = results[0]
        assert isinstance(chunk, SectionChunk)
        assert chunk.section_title == "Risk Factors"
        assert score > 0

    def test_index_and_search_table_chunk(
        self,
        lancedb_dir: Path,
        sample_table_chunk: TableChunk,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        store.index_table_chunk(sample_table_chunk, sample_embedding)

        results = store.search(sample_embedding, top_k=5)
        assert len(results) == 1
        chunk, score = results[0]
        assert isinstance(chunk, TableChunk)
        assert chunk.section_title == "Revenue Breakdown"

    def test_search_empty_store(self, lancedb_dir: Path) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        results = store.search([0.1, 0.2, 0.3], top_k=5)
        assert results == []

    def test_chunk_count(
        self,
        lancedb_dir: Path,
        sample_section_chunk: SectionChunk,
        sample_table_chunk: TableChunk,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        assert store.chunk_count == 0

        store.index_section_chunk(sample_section_chunk, sample_embedding)
        assert store.chunk_count == 1

        store.index_table_chunk(sample_table_chunk, sample_embedding)
        assert store.chunk_count == 2

    def test_filtered_search_by_doc_id(
        self,
        lancedb_dir: Path,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        doc_id_1 = UUID("00000000-0000-0000-0000-000000000001")
        doc_id_2 = UUID("00000000-0000-0000-0000-000000000002")

        chunk_1 = SectionChunk(
            doc_id=doc_id_1, section_title="A", text="text a", token_count=2
        )
        chunk_2 = SectionChunk(
            doc_id=doc_id_2, section_title="B", text="text b", token_count=2
        )
        store.index_section_chunk(chunk_1, sample_embedding)
        store.index_section_chunk(chunk_2, [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])

        results = store.search(sample_embedding, top_k=5, doc_ids=[doc_id_1])
        assert len(results) == 1
        assert results[0][0].doc_id == doc_id_1

    def test_metadata_persistence(self, lancedb_dir: Path) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        store.save_metadata({"embedding_model": "test-model", "chunk_count": 42})

        loaded = store.load_metadata()
        assert loaded is not None
        assert loaded["embedding_model"] == "test-model"
        assert loaded["chunk_count"] == 42
        assert "built_at" in loaded

    def test_reopen_from_disk(
        self,
        lancedb_dir: Path,
        sample_section_chunk: SectionChunk,
        sample_embedding: list[float],
    ) -> None:
        """Verify data persists across store instances."""
        store1 = LanceDBRetrievalStore(lancedb_dir)
        store1.index_section_chunk(sample_section_chunk, sample_embedding)
        assert store1.chunk_count == 1

        # Re-open from the same directory
        store2 = LanceDBRetrievalStore(lancedb_dir)
        assert store2.chunk_count == 1
        results = store2.search(sample_embedding, top_k=5)
        assert len(results) == 1

    def test_delete_doc_removes_rows(
        self,
        lancedb_dir: Path,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        chunk_1 = SectionChunk(
            doc_id=UUID("00000000-0000-0000-0000-000000000001"),
            section_title="A",
            text="text a",
            token_count=2,
        )
        chunk_2 = SectionChunk(
            doc_id=UUID("00000000-0000-0000-0000-000000000002"),
            section_title="B",
            text="text b",
            token_count=2,
        )
        store.index_section_chunk(chunk_1, sample_embedding)
        store.index_section_chunk(chunk_2, sample_embedding)

        store.delete_doc(chunk_2.doc_id)

        assert store.chunk_count == 1
        results = store.search(sample_embedding, top_k=5)
        assert len(results) == 1
        assert results[0][0].doc_id == chunk_1.doc_id

    def test_segmented_rows_dedupe_back_to_source_chunk(
        self,
        lancedb_dir: Path,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        chunk = SectionChunk(
            doc_id=UUID("00000000-0000-0000-0000-000000000009"),
            section_title="MD&A",
            text="Long narrative chunk",
            token_count=3,
        )
        store.index_chunk_segments(
            chunk,
            [
                ChunkSegment(text="segment-0", segment_index=0, segment_count=2),
                ChunkSegment(text="segment-1", segment_index=1, segment_count=2),
            ],
            [sample_embedding, sample_embedding],
        )

        assert store.chunk_count == 2
        hits = store.search(sample_embedding, top_k=5)
        assert len(hits) == 1
        assert hits[0][0].chunk_id == chunk.chunk_id

        lineage_rows = store.fetch_lineage_rows()
        assert len(lineage_rows) == 2
        assert {row["source_chunk_id"] for row in lineage_rows} == {str(chunk.chunk_id)}
        assert {row["segment_index"] for row in lineage_rows} == {0, 1}

    def test_add_rows_persists_prebuilt_segment_rows(
        self,
        lancedb_dir: Path,
        sample_embedding: list[float],
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        chunk = SectionChunk(
            doc_id=UUID("00000000-0000-0000-0000-000000000011"),
            section_title="MD&A",
            text="Long narrative chunk",
            token_count=3,
        )
        rows = store.build_chunk_segment_rows(
            chunk,
            [
                ChunkSegment(text="segment-0", segment_index=0, segment_count=2),
                ChunkSegment(text="segment-1", segment_index=1, segment_count=2),
            ],
            [sample_embedding, sample_embedding],
        )

        store.add_rows(rows)

        assert store.chunk_count == 2
        hits = store.search(sample_embedding, top_k=5)
        assert len(hits) == 1
        assert hits[0][0].chunk_id == chunk.chunk_id

    def test_search_overfetches_until_top_k_unique_source_chunks(
        self,
        lancedb_dir: Path,
    ) -> None:
        store = LanceDBRetrievalStore(lancedb_dir)
        dominant = SectionChunk(
            doc_id=UUID("00000000-0000-0000-0000-000000000010"),
            section_title="Dominant",
            text="dominant chunk",
            token_count=2,
        )
        store.index_chunk_segments(
            dominant,
            [
                ChunkSegment(text=f"segment-{index}", segment_index=index, segment_count=20)
                for index in range(20)
            ],
            [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(20)],
        )

        for index in range(4):
            other = SectionChunk(
                doc_id=UUID(f"00000000-0000-0000-0000-00000000002{index}"),
                section_title=f"Other {index}",
                text=f"other chunk {index}",
                token_count=3,
            )
            store.index_section_chunk(
                other,
                [0.99 - (index * 0.01), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            )

        hits = store.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=4)

        assert len(hits) == 4
        assert hits[0][0].section_title == "Dominant"
        assert {hit[0].section_title for hit in hits[1:]} == {
            "Other 0",
            "Other 1",
            "Other 2",
        }


# ---------------------------------------------------------------------------
# Runtime validation tests
# ---------------------------------------------------------------------------


class TestRuntimeLanceDBValidation:
    def test_missing_lancedb_dir_raises(self, tmp_path: Path) -> None:
        """Runtime validation should fail when lancedb dir is missing."""
        processed = tmp_path / "processed"
        processed.mkdir()
        (processed / "filings").mkdir()
        (processed / "chunks").mkdir()
        (processed / "tables").mkdir()
        facts_dir = processed / "facts"
        facts_dir.mkdir()
        (facts_dir / "all_facts.jsonl").touch()
        # Deliberately do NOT create lancedb dir.

        with pytest.raises(MissingProcessedArtifactError, match="lancedb"):
            validate_processed_dir(processed)
