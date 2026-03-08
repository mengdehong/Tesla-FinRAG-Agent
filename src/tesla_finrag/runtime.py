"""Processed-corpus runtime bootstrap.

Loads normalized artifacts from ``data/processed/`` into the in-memory
repository layer so that app, evaluation, and CLI surfaces all share the
same processed corpus at startup.

Expected artifact layout (produced by ``tesla_finrag.ingestion.writers``)::

    data/processed/
    ├── filings/              # One JSON per FilingDocument
    │   └── <doc_id>.json
    ├── chunks/               # Narrative section chunks
    │   └── <doc_id>/
    │       └── <chunk_id>.json
    ├── tables/               # Extracted table chunks
    │   └── <doc_id>/
    │       └── <chunk_id>.json
    └── facts/
        └── all_facts.jsonl   # One FactRecord JSON object per line
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tesla_finrag.models import (
    FactRecord,
    FilingDocument,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.retrieval import InMemoryCorpusRepository, InMemoryFactsRepository
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore
from tesla_finrag.settings import get_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROCESSED_DIR = _PROJECT_ROOT / "data" / "processed"

# Artifact sub-paths the runtime requires.
_FILINGS_DIR = "filings"
_CHUNKS_DIR = "chunks"
_TABLES_DIR = "tables"
_FACTS_FILE = Path("facts") / "all_facts.jsonl"
_LANCEDB_DIR = "lancedb"
_LANCEDB_METADATA_FILE = Path(_LANCEDB_DIR) / "_index_metadata.json"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProcessedCorpusError(RuntimeError):
    """Raised when processed artifacts are missing or invalid."""


class MissingProcessedArtifactError(ProcessedCorpusError):
    """A required artifact directory or file is absent."""


class MalformedProcessedArtifactError(ProcessedCorpusError):
    """An artifact exists but cannot be parsed into the expected model."""


class IncompatibleIndexError(ProcessedCorpusError):
    """The LanceDB index was built with an incompatible embedding model."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def resolve_processed_dir(processed_dir: str | Path | None = None) -> Path:
    """Resolve the processed corpus root from arg, settings, or repo default."""
    if processed_dir is not None:
        return Path(processed_dir).expanduser()
    settings = get_settings()
    return Path(settings.processed_data_dir).expanduser()


def _require_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        raise MissingProcessedArtifactError(
            f"Required processed artifact directory not found: {path} ({label}). "
            "Run the ingestion pipeline first to generate processed data."
        )


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise MissingProcessedArtifactError(
            f"Required processed artifact file not found: {path} ({label}). "
            "Run the ingestion pipeline first to generate processed data."
        )


def validate_processed_dir(processed_dir: Path) -> None:
    """Check that all required processed artifact paths exist.

    Raises :class:`MissingProcessedArtifactError` on the first missing path.
    """
    _require_dir(processed_dir, "processed root")
    _require_dir(processed_dir / _FILINGS_DIR, "filings")
    _require_dir(processed_dir / _CHUNKS_DIR, "chunks")
    _require_dir(processed_dir / _TABLES_DIR, "tables")
    _require_file(processed_dir / _FACTS_FILE, "facts")
    _require_dir(processed_dir / _LANCEDB_DIR, "lancedb index")
    _require_file(processed_dir / _LANCEDB_METADATA_FILE, "lancedb metadata")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_filings(filings_dir: Path) -> list[FilingDocument]:
    filings: list[FilingDocument] = []
    for path in sorted(filings_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            filings.append(FilingDocument.model_validate(data))
        except Exception as exc:
            raise MalformedProcessedArtifactError(
                f"Failed to parse filing from {path}: {exc}"
            ) from exc
    return filings


def _load_section_chunks(chunks_dir: Path) -> list[SectionChunk]:
    chunks: list[SectionChunk] = []
    for doc_dir in sorted(chunks_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        for path in sorted(doc_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chunks.append(SectionChunk.model_validate(data))
            except Exception as exc:
                raise MalformedProcessedArtifactError(
                    f"Failed to parse section chunk from {path}: {exc}"
                ) from exc
    return chunks


def _load_table_chunks(tables_dir: Path) -> list[TableChunk]:
    chunks: list[TableChunk] = []
    for doc_dir in sorted(tables_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        for path in sorted(doc_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chunks.append(TableChunk.model_validate(data))
            except Exception as exc:
                raise MalformedProcessedArtifactError(
                    f"Failed to parse table chunk from {path}: {exc}"
                ) from exc
    return chunks


def _load_facts(facts_path: Path) -> list[FactRecord]:
    facts: list[FactRecord] = []
    with open(facts_path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                facts.append(FactRecord.model_validate(data))
            except Exception as exc:
                raise MalformedProcessedArtifactError(
                    f"Failed to parse fact record at {facts_path}:{line_no}: {exc}"
                ) from exc
    return facts


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def load_processed_corpus(
    processed_dir: str | Path | None = None,
) -> tuple[InMemoryCorpusRepository, InMemoryFactsRepository, LanceDBRetrievalStore]:
    """Load processed artifacts into in-memory repositories.

    Args:
        processed_dir: Optional root directory containing processed artifacts.
            If omitted, runtime settings are used.

    Returns:
        A ``(corpus_repo, facts_repo, retrieval_store)`` tuple ready for use
        by the workbench pipeline.

    Raises:
        MissingProcessedArtifactError: A required directory or file is absent.
        MalformedProcessedArtifactError: An artifact cannot be parsed.
        IncompatibleIndexError: The LanceDB index embedding model does not
            match the current indexing configuration.
    """
    resolved_dir = resolve_processed_dir(processed_dir)
    validate_processed_dir(resolved_dir)

    filings = _load_filings(resolved_dir / _FILINGS_DIR)
    section_chunks = _load_section_chunks(resolved_dir / _CHUNKS_DIR)
    table_chunks = _load_table_chunks(resolved_dir / _TABLES_DIR)
    facts = _load_facts(resolved_dir / _FACTS_FILE)

    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()

    for filing in filings:
        corpus_repo.upsert_filing(filing)

    for chunk in section_chunks:
        corpus_repo.upsert_section_chunk(chunk)

    for chunk in table_chunks:
        corpus_repo.upsert_table_chunk(chunk)

    for fact in facts:
        facts_repo.upsert_fact(fact)

    # Open persisted LanceDB retrieval store.
    lancedb_path = resolved_dir / _LANCEDB_DIR
    retrieval_store = LanceDBRetrievalStore(lancedb_path)
    meta = retrieval_store.load_metadata()
    if meta is None:
        raise MalformedProcessedArtifactError(
            f"Failed to parse LanceDB metadata at {retrieval_store.metadata_path}."
        )

    if not retrieval_store.has_table:
        raise MissingProcessedArtifactError(
            f"Required LanceDB chunks table not found in: {lancedb_path}. "
            "Run the ingestion pipeline first to generate processed data."
        )

    expected_chunk_count = len(section_chunks) + len(table_chunks)
    if retrieval_store.chunk_count != expected_chunk_count:
        raise MalformedProcessedArtifactError(
            "LanceDB index chunk count does not match the processed corpus: "
            f"expected {expected_chunk_count}, found {retrieval_store.chunk_count}. "
            "Re-run the ingestion pipeline to rebuild the index."
        )

    settings = get_settings()
    stored_model = meta.get("embedding_model", "")
    if stored_model and stored_model != settings.indexing_embedding_model:
        raise IncompatibleIndexError(
            f"LanceDB index was built with embedding model '{stored_model}' "
            f"but current configuration uses '{settings.indexing_embedding_model}'. "
            "Re-run the ingestion pipeline to rebuild the index."
        )

    logger.info(
        "Loaded processed corpus: %d filings, %d section chunks, "
        "%d table chunks, %d facts, LanceDB chunks: %d",
        len(filings),
        len(section_chunks),
        len(table_chunks),
        len(facts),
        retrieval_store.chunk_count,
    )

    return corpus_repo, facts_repo, retrieval_store
