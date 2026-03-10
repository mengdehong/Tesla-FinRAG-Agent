"""Tests for the processed-corpus runtime bootstrap module."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from tesla_finrag.ingestion.index_segmentation import ChunkSegment
from tesla_finrag.models import (
    FactRecord,
    FilingDocument,
    FilingType,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore
from tesla_finrag.runtime import (
    IncompatibleIndexError,
    MalformedProcessedArtifactError,
    MissingProcessedArtifactError,
    load_processed_corpus,
    resolve_processed_dir,
    validate_processed_dir,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")


def _build_valid_fixture(root: Path) -> tuple[FilingDocument, SectionChunk, TableChunk, FactRecord]:
    """Create a minimal valid processed corpus on disk and return the models."""
    filing = FilingDocument(
        filing_type=FilingType.QUARTERLY,
        period_end="2023-03-31",
        fiscal_year=2023,
        fiscal_quarter=1,
        accession_number="0000950170-2023-01",
        filed_at="2023-04-15",
        source_path="data/raw/Tesla_2023_Q1_10-Q.pdf",
    )
    section = SectionChunk(
        doc_id=filing.doc_id,
        section_title="MD&A",
        text="Tesla expanded manufacturing capacity.",
        token_count=5,
        page_number=10,
    )
    table = TableChunk(
        doc_id=filing.doc_id,
        section_title="Revenue",
        headers=["Segment", "Amount"],
        rows=[["Automotive", "19963"]],
        raw_text="Segment | Amount\nAutomotive | 19963",
    )
    fact = FactRecord(
        doc_id=filing.doc_id,
        concept="us-gaap:Revenues",
        label="Total Revenues",
        value=23329.0,
        unit="USD",
        scale=1_000_000,
        period_end="2023-03-31",
    )

    _write_json(root / "filings" / f"{filing.doc_id}.json", filing.model_dump(mode="json"))
    _write_json(
        root / "chunks" / str(filing.doc_id) / f"{section.chunk_id}.json",
        section.model_dump(mode="json"),
    )
    _write_json(
        root / "tables" / str(filing.doc_id) / f"{table.chunk_id}.json",
        table.model_dump(mode="json"),
    )
    _write_jsonl(root / "facts" / "all_facts.jsonl", [fact.model_dump(mode="json")])
    store = LanceDBRetrievalStore(root / "lancedb")
    embedding = [0.1, 0.2, 0.3]
    store.index_section_chunk(section, embedding)
    store.index_table_chunk(table, embedding)
    store.save_metadata(
        {
            "index_schema_version": 2,
            "embedding_model": "nomic-embed-text",
            "embedding_base_url": "http://localhost:11434/v1",
            "embedding_dimensions": len(embedding),
            "source_chunk_count": 2,
            "vector_row_count": 2,
            "chunk_count": 2,
        }
    )

    return filing, section, table, fact


# ---------------------------------------------------------------------------
# Valid corpus
# ---------------------------------------------------------------------------


class TestLoadValidCorpus:
    def test_default_processed_dir_is_repo_relative(self) -> None:
        resolved = resolve_processed_dir()
        assert resolved.name == "processed"
        assert resolved.parent.name == "data"
        assert resolved.is_absolute()

    def test_env_processed_dir_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROCESSED_DATA_DIR", str(tmp_path))
        from tesla_finrag.settings import get_settings

        get_settings.cache_clear()
        try:
            assert resolve_processed_dir() == tmp_path
        finally:
            get_settings.cache_clear()

    def test_loads_filing(self, tmp_path: Path) -> None:
        filing, _, _, _ = _build_valid_fixture(tmp_path)
        corpus_repo, _, _ = load_processed_corpus(tmp_path)
        loaded = corpus_repo.get_filing(filing.doc_id)
        assert loaded is not None
        assert loaded.doc_id == filing.doc_id

    def test_loads_section_chunk(self, tmp_path: Path) -> None:
        filing, section, _, _ = _build_valid_fixture(tmp_path)
        corpus_repo, _, _ = load_processed_corpus(tmp_path)
        chunks = corpus_repo.get_section_chunks(filing.doc_id)
        assert len(chunks) == 1
        assert chunks[0].chunk_id == section.chunk_id

    def test_loads_table_chunk(self, tmp_path: Path) -> None:
        filing, _, table, _ = _build_valid_fixture(tmp_path)
        corpus_repo, _, _ = load_processed_corpus(tmp_path)
        chunks = corpus_repo.get_table_chunks(filing.doc_id)
        assert len(chunks) == 1
        assert chunks[0].chunk_id == table.chunk_id

    def test_loads_facts(self, tmp_path: Path) -> None:
        filing, _, _, fact = _build_valid_fixture(tmp_path)
        _, facts_repo, _ = load_processed_corpus(tmp_path)
        facts = facts_repo.get_facts(doc_id=filing.doc_id)
        assert len(facts) == 1
        assert facts[0].concept == fact.concept

    def test_load_accepts_segmented_lancedb_index(self, tmp_path: Path) -> None:
        filing, section, table, _ = _build_valid_fixture(tmp_path)
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        store.clear()
        embedding = [0.1, 0.2, 0.3]
        store.index_chunk_segments(
            section,
            [
                ChunkSegment(text="segment-a", segment_index=0, segment_count=2),
                ChunkSegment(text="segment-b", segment_index=1, segment_count=2),
            ],
            [embedding, embedding],
        )
        store.index_table_chunk(table, embedding)
        store.save_metadata(
            {
                "index_schema_version": 2,
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": len(embedding),
                "source_chunk_count": 2,
                "vector_row_count": 3,
                "chunk_count": 3,
            }
        )

        corpus_repo, _, retrieval_store = load_processed_corpus(tmp_path)
        assert corpus_repo.get_filing(filing.doc_id) is not None
        assert retrieval_store.chunk_count == 3


# ---------------------------------------------------------------------------
# Missing artifacts
# ---------------------------------------------------------------------------


class TestMissingArtifacts:
    def test_missing_root_dir(self, tmp_path: Path) -> None:
        with pytest.raises(MissingProcessedArtifactError, match="processed root"):
            validate_processed_dir(tmp_path / "nonexistent")

    def test_missing_filings_dir(self, tmp_path: Path) -> None:
        (tmp_path / "chunks").mkdir()
        (tmp_path / "tables").mkdir()
        (tmp_path / "facts").mkdir()
        (tmp_path / "facts" / "all_facts.jsonl").touch()
        with pytest.raises(MissingProcessedArtifactError, match="filings"):
            validate_processed_dir(tmp_path)

    def test_missing_chunks_dir(self, tmp_path: Path) -> None:
        (tmp_path / "filings").mkdir()
        (tmp_path / "tables").mkdir()
        (tmp_path / "facts").mkdir()
        (tmp_path / "facts" / "all_facts.jsonl").touch()
        with pytest.raises(MissingProcessedArtifactError, match="chunks"):
            validate_processed_dir(tmp_path)

    def test_missing_tables_dir(self, tmp_path: Path) -> None:
        (tmp_path / "filings").mkdir()
        (tmp_path / "chunks").mkdir()
        (tmp_path / "facts").mkdir()
        (tmp_path / "facts" / "all_facts.jsonl").touch()
        with pytest.raises(MissingProcessedArtifactError, match="tables"):
            validate_processed_dir(tmp_path)

    def test_missing_facts_file(self, tmp_path: Path) -> None:
        (tmp_path / "filings").mkdir()
        (tmp_path / "chunks").mkdir()
        (tmp_path / "tables").mkdir()
        (tmp_path / "facts").mkdir()
        with pytest.raises(MissingProcessedArtifactError, match="facts"):
            validate_processed_dir(tmp_path)

    def test_missing_lancedb_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "filings").mkdir()
        (tmp_path / "chunks").mkdir()
        (tmp_path / "tables").mkdir()
        (tmp_path / "facts").mkdir()
        (tmp_path / "facts" / "all_facts.jsonl").touch()
        (tmp_path / "lancedb").mkdir()
        with pytest.raises(MissingProcessedArtifactError, match="lancedb metadata"):
            validate_processed_dir(tmp_path)

    def test_load_raises_for_missing(self, tmp_path: Path) -> None:
        with pytest.raises(MissingProcessedArtifactError):
            load_processed_corpus(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# Malformed artifacts
# ---------------------------------------------------------------------------


class TestMalformedArtifacts:
    def test_malformed_filing_json(self, tmp_path: Path) -> None:
        _build_valid_fixture(tmp_path)
        bad_path = tmp_path / "filings" / f"{uuid4()}.json"
        bad_path.write_text('{"not": "a filing"}', encoding="utf-8")
        with pytest.raises(MalformedProcessedArtifactError, match="filing"):
            load_processed_corpus(tmp_path)

    def test_malformed_chunk_json(self, tmp_path: Path) -> None:
        filing, _, _, _ = _build_valid_fixture(tmp_path)
        bad_dir = tmp_path / "chunks" / str(filing.doc_id)
        bad_path = bad_dir / f"{uuid4()}.json"
        bad_path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(MalformedProcessedArtifactError, match="section chunk"):
            load_processed_corpus(tmp_path)

    def test_malformed_table_json(self, tmp_path: Path) -> None:
        filing, _, _, _ = _build_valid_fixture(tmp_path)
        bad_dir = tmp_path / "tables" / str(filing.doc_id)
        bad_path = bad_dir / f"{uuid4()}.json"
        bad_path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(MalformedProcessedArtifactError, match="table chunk"):
            load_processed_corpus(tmp_path)

    def test_malformed_fact_line(self, tmp_path: Path) -> None:
        _build_valid_fixture(tmp_path)
        facts_path = tmp_path / "facts" / "all_facts.jsonl"
        with open(facts_path, "a", encoding="utf-8") as fh:
            fh.write("NOT VALID JSON\n")
        with pytest.raises(MalformedProcessedArtifactError, match="fact record"):
            load_processed_corpus(tmp_path)

    def test_missing_lancedb_table_raises(self, tmp_path: Path) -> None:
        _build_valid_fixture(tmp_path)
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        store._db.drop_table("chunks")  # type: ignore[attr-defined]
        with pytest.raises(MissingProcessedArtifactError, match="chunks table"):
            load_processed_corpus(tmp_path)

    def test_lancedb_orphaned_lineage_raises(self, tmp_path: Path) -> None:
        _build_valid_fixture(tmp_path)
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        section = SectionChunk(
            doc_id=uuid4(),
            section_title="Extra",
            text="stale row",
            token_count=2,
        )
        store.index_section_chunk(section, [0.9, 0.8, 0.7])
        store.save_metadata(
            {
                "index_schema_version": 2,
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": 3,
                "source_chunk_count": 3,
                "vector_row_count": store.chunk_count,
                "chunk_count": store.chunk_count,
            }
        )
        with pytest.raises(MalformedProcessedArtifactError, match="orphaned lineage"):
            load_processed_corpus(tmp_path)

    def test_lancedb_duplicate_segment_ordinal_raises(self, tmp_path: Path) -> None:
        _, section, table, _ = _build_valid_fixture(tmp_path)
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        store.clear()
        embedding = [0.1, 0.2, 0.3]
        store.index_chunk_segments(
            section,
            [
                ChunkSegment(text="segment-a", segment_index=0, segment_count=2),
                ChunkSegment(text="segment-b", segment_index=0, segment_count=2),
            ],
            [embedding, embedding],
        )
        store.index_table_chunk(table, embedding)
        store.save_metadata(
            {
                "index_schema_version": 2,
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": 3,
                "source_chunk_count": 2,
                "vector_row_count": 3,
                "chunk_count": 3,
            }
        )
        with pytest.raises(MalformedProcessedArtifactError, match="duplicate segment ordinals"):
            load_processed_corpus(tmp_path)

    def test_incompatible_index_model_raises(self, tmp_path: Path) -> None:
        _build_valid_fixture(tmp_path)
        store = LanceDBRetrievalStore(tmp_path / "lancedb")
        store.save_metadata(
            {
                "index_schema_version": 2,
                "embedding_model": "different-model",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": 3,
                "source_chunk_count": 2,
                "vector_row_count": store.chunk_count,
                "chunk_count": store.chunk_count,
            }
        )
        with pytest.raises(IncompatibleIndexError, match="different-model"):
            load_processed_corpus(tmp_path)
