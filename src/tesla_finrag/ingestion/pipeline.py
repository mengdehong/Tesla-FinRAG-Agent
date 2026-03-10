"""End-to-end ingestion pipeline runner.

Orchestrates the full dual-track ingestion from ``data/raw/`` to
``data/processed/``, producing the normalised corpus and reporting
coverage gaps explicitly.
"""

from __future__ import annotations

import json as jsonlib
import multiprocessing as mp
import os
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import monotonic
from uuid import UUID

from tesla_finrag.ingestion.analysis import analyze_filing_pdf
from tesla_finrag.ingestion.index_segmentation import segment_chunk_for_indexing
from tesla_finrag.ingestion.manifest import build_manifest, print_manifest_summary
from tesla_finrag.ingestion.narrative import narrative_chunks_from_analysis
from tesla_finrag.ingestion.source_adapter import period_key_from_doc, resolve_all_filings
from tesla_finrag.ingestion.state import (
    FactsStateEntry,
    FilingStateEntry,
    IngestionState,
    fingerprint_file,
    fingerprint_modules,
    load_ingestion_state,
    save_ingestion_state,
)
from tesla_finrag.ingestion.tables import table_chunks_from_analysis
from tesla_finrag.ingestion.validation import reconcile_table_with_facts
from tesla_finrag.ingestion.writers import (
    remove_filing_artifacts,
    write_facts,
    write_filing_bundle,
    write_manifest,
)
from tesla_finrag.ingestion.xbrl import normalize_companyfacts, summarize_facts
from tesla_finrag.logging_config import get_logger, suppress_pdfminer_font_warnings
from tesla_finrag.models import (
    FactRecord,
    FilingDocument,
    SectionChunk,
    TableChunk,
    ValidationStatus,
)
from tesla_finrag.provider import IndexingEmbeddingProvider
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore

logger = get_logger(__name__)

_FILING_PARSER_FINGERPRINT = fingerprint_modules(
    (
        Path(__file__).with_name("analysis.py"),
        Path(__file__).with_name("narrative.py"),
        Path(__file__).with_name("tables.py"),
    ),
    version_tag="filing-parser-v1",
)
_FACTS_PARSER_FINGERPRINT = fingerprint_modules(
    (
        Path(__file__).with_name("xbrl.py"),
        Path(__file__).with_name("source_adapter.py"),
    ),
    version_tag="facts-parser-v1",
)


@dataclass(frozen=True)
class FilingIngestionResult:
    """Structured output for a single filing ingestion job."""

    index: int
    doc_id: UUID
    period_key: str
    source_path: str
    section_chunks: list[dict]
    table_chunks: list[dict]
    elapsed_seconds: float
    error: str | None = None
    fallback_pages: int = 0
    failed_pages: int = 0
    validation_failed_tables: int = 0
    validation_suspect_tables: int = 0
    page_diagnostics: list[dict] | None = None


@dataclass(frozen=True)
class FilingExecutionPlan:
    """Execution decision for a filing in the current run."""

    filing: FilingDocument
    pdf_path: Path | None
    source_fingerprint: str | None
    reuse_existing: bool
    reason: str


def _resolve_source_pdf_path(raw_dir: Path, source_path: str) -> Path | None:
    """Resolve a filing PDF path from either repo-relative or local raw paths."""
    source = Path(source_path)
    candidates: list[Path] = []

    if source.is_absolute():
        candidates.append(source)
    else:
        candidates.append(raw_dir / source.name)
        candidates.extend(parent / source for parent in (raw_dir, *raw_dir.parents))

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    return None


def _filing_artifacts_exist(output_dir: Path, doc_id: UUID) -> bool:
    """Check whether the processed corpus already contains a filing's artifacts."""
    return (
        (output_dir / "filings" / f"{doc_id}.json").is_file()
        and (output_dir / "chunks" / str(doc_id)).is_dir()
        and (output_dir / "tables" / str(doc_id)).is_dir()
    )


def _facts_artifact_exists(output_dir: Path) -> bool:
    """Return whether normalized facts output is present."""
    return (output_dir / "facts" / "all_facts.jsonl").is_file()


def _load_facts_from_disk(output_dir: Path) -> list[FactRecord]:
    """Load previously persisted fact records from ``all_facts.jsonl``."""
    facts_path = output_dir / "facts" / "all_facts.jsonl"
    if not facts_path.is_file():
        return []
    facts: list[FactRecord] = []
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            facts.append(FactRecord.model_validate(jsonlib.loads(line)))
    return facts


def _load_tables_from_disk(
    output_dir: Path,
    doc_ids: list[UUID],
) -> dict[UUID, list[TableChunk]]:
    """Load persisted table artifacts for the requested filings."""
    tables: dict[UUID, list[TableChunk]] = {}
    for doc_id in doc_ids:
        tables_dir = output_dir / "tables" / str(doc_id)
        chunks: list[TableChunk] = []
        if tables_dir.is_dir():
            for path in sorted(tables_dir.glob("*.json")):
                data = jsonlib.loads(path.read_text(encoding="utf-8"))
                chunks.append(TableChunk.model_validate(data))
        tables[doc_id] = chunks
    return tables


def _remediation_for_page_diagnostic(error: str | None, *, used_fallback: bool) -> str:
    """Return operator guidance for a page-level parser diagnostic."""
    if error:
        if error.startswith("no_fallback_available:"):
            return (
                "Install the optional fallback parser (`uv sync --extra fallback`) or "
                "review the source PDF manually."
            )
        if error.startswith("fallback_also_empty:"):
            return "Review the source PDF page manually; no parser produced usable text."
        if error.startswith("fallback_error:"):
            return "Inspect PyMuPDF availability/logs and review the source PDF page manually."
        return "Review the source PDF page manually and inspect parser logs."
    if used_fallback:
        return "Fallback parsing was used; review this page before citing extracted evidence."
    return ""


def _page_diagnostic_entry(
    *,
    period_key: str,
    source_path: str,
    page_number: int,
    parser_used: str,
    used_fallback: bool,
    fallback_reason: str | None,
    error: str | None,
) -> dict:
    """Build a structured, operator-facing parser diagnostic record."""
    parser_attempts = ["pdfplumber"]
    if used_fallback:
        parser_attempts.append("pymupdf")
    elif error and error.startswith("no_fallback_available:"):
        parser_attempts.append("pymupdf(unavailable)")
    elif error and error.startswith(("fallback_also_empty:", "fallback_error:")):
        parser_attempts.append("pymupdf")

    return {
        "period_key": period_key,
        "source_path": source_path,
        "artifact_type": "page",
        "page_number": page_number,
        "parser_used": parser_used,
        "parser_attempts": parser_attempts,
        "used_fallback": used_fallback,
        "fallback_reason": fallback_reason,
        "error": error,
        "remediation": _remediation_for_page_diagnostic(error, used_fallback=used_fallback),
    }


def _reconcile_filing_tables(
    all_table_chunks: dict[UUID, list[TableChunk]],
    filings: list[FilingDocument],
    facts: list[FactRecord],
) -> tuple[dict[UUID, list[TableChunk]], int]:
    """Reconcile table chunks against authoritative XBRL facts.

    Returns the updated table chunk mapping and the total number of mismatches found.
    """
    if not facts:
        return all_table_chunks, 0

    # Build a doc_id -> period_end lookup from the filing list.
    doc_period: dict[UUID, date] = {f.doc_id: f.period_end for f in filings}

    total_mismatches = 0
    for doc_id, tables in all_table_chunks.items():
        period_end = doc_period.get(doc_id)
        updated_tables: list[TableChunk] = []
        for table in tables:
            reconciliations = reconcile_table_with_facts(table, facts, period_end=period_end)
            if reconciliations:
                mismatches = sum(1 for r in reconciliations if not r.matched)
                total_mismatches += mismatches
                update: dict[str, object] = {"fact_reconciliations": reconciliations}
                if mismatches > 0:
                    update["validation_status"] = ValidationStatus.FAILED
                table = table.model_copy(update=update)
            updated_tables.append(table)
        all_table_chunks[doc_id] = updated_tables

    return all_table_chunks, total_mismatches


def _resolve_worker_count(requested_workers: int, active_filings: int) -> int:
    """Choose worker count from explicit override or active workload size."""
    if requested_workers > 0:
        return requested_workers
    if active_filings <= 0:
        return 1
    return max(1, min(4, os.cpu_count() or 1, active_filings))


def _plan_filing_jobs(
    filings: list[FilingDocument],
    raw_dir: Path,
    output_dir: Path,
    state: IngestionState,
) -> list[FilingExecutionPlan]:
    """Decide whether each filing can be reused or must be reparsed."""
    plans: list[FilingExecutionPlan] = []

    for filing in filings:
        pdf_path = _resolve_source_pdf_path(raw_dir, filing.source_path)
        if pdf_path is None:
            plans.append(
                FilingExecutionPlan(
                    filing=filing,
                    pdf_path=None,
                    source_fingerprint=None,
                    reuse_existing=False,
                    reason="missing_source",
                )
            )
            continue

        source_fingerprint = fingerprint_file(pdf_path)
        existing_state = state.filings.get(str(filing.doc_id))
        artifacts_exist = _filing_artifacts_exist(output_dir, filing.doc_id)

        if existing_state is None:
            reason = "missing_state"
        elif not artifacts_exist:
            reason = "missing_artifacts"
        elif existing_state.source_path != filing.source_path:
            reason = "source_path_changed"
        elif existing_state.source_fingerprint != source_fingerprint:
            reason = "source_changed"
        elif existing_state.parser_fingerprint != _FILING_PARSER_FINGERPRINT:
            reason = "parser_changed"
        else:
            reason = "unchanged"

        plans.append(
            FilingExecutionPlan(
                filing=filing,
                pdf_path=pdf_path,
                source_fingerprint=source_fingerprint,
                reuse_existing=reason == "unchanged",
                reason=reason,
            )
        )

    return plans


def _ingest_single_filing(
    index: int,
    filing_data: dict,
    raw_dir: str,
) -> FilingIngestionResult:
    """Parse narrative and tables for a single filing."""
    suppress_pdfminer_font_warnings()

    filing = FilingDocument.model_validate(filing_data)
    start = monotonic()
    pdf_path = _resolve_source_pdf_path(Path(raw_dir), filing.source_path)

    if pdf_path is None:
        return FilingIngestionResult(
            index=index,
            doc_id=filing.doc_id,
            period_key=period_key_from_doc(filing),
            source_path=filing.source_path,
            section_chunks=[],
            table_chunks=[],
            elapsed_seconds=monotonic() - start,
            error=f"PDF not found: {filing.source_path}",
        )

    try:
        analysis = analyze_filing_pdf(pdf_path)
        sections = narrative_chunks_from_analysis(analysis, filing.doc_id)
        tables = table_chunks_from_analysis(analysis, filing.doc_id)

        # Collect diagnostics from the analysis.
        fallback_pages = analysis.fallback_count
        failed_pages = len(analysis.failed_pages)

        validation_failed = sum(1 for t in tables if t.validation_status == ValidationStatus.FAILED)
        validation_suspect = sum(
            1 for t in tables if t.validation_status == ValidationStatus.SUSPECT
        )
        page_diagnostics = [
            _page_diagnostic_entry(
                period_key=period_key_from_doc(filing),
                source_path=str(pdf_path),
                page_number=diag.page_number,
                parser_used=diag.parser_used,
                used_fallback=diag.used_fallback,
                fallback_reason=diag.fallback_reason,
                error=diag.error,
            )
            for diag in analysis.diagnostics
            if diag.used_fallback or diag.error
        ]

        return FilingIngestionResult(
            index=index,
            doc_id=filing.doc_id,
            period_key=period_key_from_doc(filing),
            source_path=str(pdf_path),
            section_chunks=[chunk.model_dump(mode="json") for chunk in sections],
            table_chunks=[chunk.model_dump(mode="json") for chunk in tables],
            elapsed_seconds=monotonic() - start,
            fallback_pages=fallback_pages,
            failed_pages=failed_pages,
            validation_failed_tables=validation_failed,
            validation_suspect_tables=validation_suspect,
            page_diagnostics=page_diagnostics,
        )
    except Exception as exc:
        return FilingIngestionResult(
            index=index,
            doc_id=filing.doc_id,
            period_key=period_key_from_doc(filing),
            source_path=str(pdf_path),
            section_chunks=[],
            table_chunks=[],
            elapsed_seconds=monotonic() - start,
            error=str(exc),
        )


def _log_filing_result(result: FilingIngestionResult, total: int) -> None:
    prefix = f"[{result.index}/{total}] {result.period_key}"
    if result.error:
        logger.error("%s failed in %.1fs: %s", prefix, result.elapsed_seconds, result.error)
        return

    diag_parts: list[str] = []
    if result.fallback_pages > 0:
        diag_parts.append(f"fallback_pages={result.fallback_pages}")
    if result.failed_pages > 0:
        diag_parts.append(f"failed_pages={result.failed_pages}")
    if result.validation_failed_tables > 0:
        diag_parts.append(f"validation_failed_tables={result.validation_failed_tables}")
    if result.validation_suspect_tables > 0:
        diag_parts.append(f"validation_suspect_tables={result.validation_suspect_tables}")
    diag_suffix = f" ({', '.join(diag_parts)})" if diag_parts else ""

    logger.info(
        "%s done in %.1fs: %d narrative chunks, %d table chunks%s",
        prefix,
        result.elapsed_seconds,
        len(result.section_chunks),
        len(result.table_chunks),
        diag_suffix,
    )


def _run_filing_ingestion_jobs(
    filings: list[FilingDocument],
    raw_dir: Path,
    *,
    workers: int,
) -> tuple[
    dict[UUID, list[SectionChunk]], dict[UUID, list[TableChunk]], list[dict], dict[str, object]
]:
    """Run filing-level PDF ingestion sequentially or in parallel."""
    total = len(filings)
    all_section_chunks: dict[UUID, list[SectionChunk]] = {}
    all_table_chunks: dict[UUID, list[TableChunk]] = {}
    failed_details: list[dict] = []
    diagnostics_agg: dict[str, object] = {
        "fallback_pages": 0,
        "failed_pages": 0,
        "validation_failed_tables": 0,
        "validation_suspect_tables": 0,
        "parser_diagnostic_details": [],
    }

    if total == 0:
        return all_section_chunks, all_table_chunks, failed_details, diagnostics_agg

    logger.info("Processing %d filings with %d worker(s)...", total, workers)

    def consume(result: FilingIngestionResult) -> None:
        _log_filing_result(result, total)
        all_section_chunks[result.doc_id] = [
            SectionChunk.model_validate(chunk) for chunk in result.section_chunks
        ]
        all_table_chunks[result.doc_id] = [
            TableChunk.model_validate(chunk) for chunk in result.table_chunks
        ]
        diagnostics_agg["fallback_pages"] += result.fallback_pages
        diagnostics_agg["failed_pages"] += result.failed_pages
        diagnostics_agg["validation_failed_tables"] += result.validation_failed_tables
        diagnostics_agg["validation_suspect_tables"] += result.validation_suspect_tables
        if result.page_diagnostics:
            details = diagnostics_agg.setdefault("parser_diagnostic_details", [])
            if isinstance(details, list):
                details.extend(result.page_diagnostics)
        if result.error:
            failed_details.append(
                {
                    "index": result.index,
                    "period_key": result.period_key,
                    "source_path": result.source_path,
                    "elapsed_seconds": round(result.elapsed_seconds, 2),
                    "error": result.error,
                }
            )

    if workers <= 1:
        for index, filing in enumerate(filings, start=1):
            consume(
                _ingest_single_filing(
                    index=index,
                    filing_data=filing.model_dump(mode="json"),
                    raw_dir=str(raw_dir),
                )
            )
        return all_section_chunks, all_table_chunks, failed_details, diagnostics_agg

    futures: dict[Future[FilingIngestionResult], FilingDocument] = {}
    # LanceDB is not fork-safe on Linux, and this module imports the store in the
    # parent process. Use spawn workers explicitly so filing parsing can still run
    # in parallel without inheriting unsafe state.
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as executor:
        for index, filing in enumerate(filings, start=1):
            future = executor.submit(
                _ingest_single_filing,
                index,
                filing.model_dump(mode="json"),
                str(raw_dir),
            )
            futures[future] = filing

        for future in as_completed(futures):
            consume(future.result())

    failed_details.sort(key=lambda item: item["index"])
    return all_section_chunks, all_table_chunks, failed_details, diagnostics_agg


# ---------------------------------------------------------------------------
# LanceDB index builder
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 64
_INDEX_WRITE_BATCH_SIZE = 1024
_INDEX_SCHEMA_VERSION = 2
_INDEX_PROGRESS_LOG_INTERVAL = 100


def _chunk_artifact_path(output_dir: Path, chunk: SectionChunk | TableChunk) -> Path:
    """Return the source processed chunk path for diagnostics."""
    artifact_dir = "chunks" if isinstance(chunk, SectionChunk) else "tables"
    return output_dir / artifact_dir / str(chunk.doc_id) / f"{chunk.chunk_id}.json"


def _chunk_kind_label(chunk: SectionChunk | TableChunk) -> str:
    return "section" if isinstance(chunk, SectionChunk) else "table"


def _embed_chunk_segments(
    embedder: IndexingEmbeddingProvider,
    chunk: SectionChunk | TableChunk,
    segments: list[str],
    *,
    output_dir: Path,
) -> list[list[float]]:
    """Embed one chunk's segments with per-segment fallback diagnostics."""
    if not segments:
        return []

    try:
        return embedder.embed_texts(segments)
    except Exception as chunk_error:
        recovered_embeddings: list[list[float]] = []
        for index, segment_text in enumerate(segments, start=1):
            try:
                recovered_embeddings.extend(embedder.embed_texts([segment_text]))
            except Exception as segment_error:
                artifact_path = _chunk_artifact_path(output_dir, chunk)
                chunk_kind = _chunk_kind_label(chunk)
                raise RuntimeError(
                    "Failed to index chunk after segmentation. "
                    f"kind={chunk_kind}, doc_id={chunk.doc_id}, chunk_id={chunk.chunk_id}, "
                    f"artifact={artifact_path}, segment={index}/{len(segments)}, "
                    f"segment_chars={len(segment_text)}. "
                    "Try reducing chunk size in the source document or adjusting the embedding "
                    "backend context settings."
                ) from segment_error
        logger.warning(
            "Recovered index embedding after segment-level retry for %s chunk %s (%s)",
            _chunk_kind_label(chunk),
            chunk.chunk_id,
            chunk_error,
        )
        return recovered_embeddings


@dataclass(frozen=True)
class _ChunkSegmentBatchItem:
    """One embedding-safe segment queued for batch indexing."""

    chunk: SectionChunk | TableChunk
    segment_text: str


def _embed_segment_batch(
    embedder: IndexingEmbeddingProvider,
    batch_items: list[_ChunkSegmentBatchItem],
    *,
    output_dir: Path,
) -> list[list[float]]:
    """Embed a mixed batch, falling back to per-chunk diagnostics on failure."""
    if not batch_items:
        return []

    try:
        return embedder.embed_texts([item.segment_text for item in batch_items])
    except Exception:
        recovered_embeddings: list[list[float]] = []
        current_chunk: SectionChunk | TableChunk | None = None
        current_segments: list[str] = []
        for item in batch_items:
            if current_chunk is None or item.chunk.chunk_id != current_chunk.chunk_id:
                if current_chunk is not None:
                    recovered_embeddings.extend(
                        _embed_chunk_segments(
                            embedder,
                            current_chunk,
                            current_segments,
                            output_dir=output_dir,
                        )
                    )
                current_chunk = item.chunk
                current_segments = [item.segment_text]
                continue
            current_segments.append(item.segment_text)

        if current_chunk is not None:
            recovered_embeddings.extend(
                _embed_chunk_segments(
                    embedder,
                    current_chunk,
                    current_segments,
                    output_dir=output_dir,
                )
            )
        return recovered_embeddings


def _load_chunks_from_disk(
    output_dir: Path,
    doc_ids: list[UUID],
) -> list[SectionChunk | TableChunk]:
    """Load persisted chunks for the requested filings."""
    chunks: list[SectionChunk | TableChunk] = []
    for doc_id in doc_ids:
        chunks_dir = output_dir / "chunks" / str(doc_id)
        if chunks_dir.is_dir():
            for path in sorted(chunks_dir.glob("*.json")):
                data = jsonlib.loads(path.read_text(encoding="utf-8"))
                chunks.append(SectionChunk.model_validate(data))

        tables_dir = output_dir / "tables" / str(doc_id)
        if tables_dir.is_dir():
            for path in sorted(tables_dir.glob("*.json")):
                data = jsonlib.loads(path.read_text(encoding="utf-8"))
                chunks.append(TableChunk.model_validate(data))
    return chunks


def _build_lancedb_index(
    output_dir: Path,
    state: IngestionState,
    *,
    refreshed_doc_ids: set[UUID],
    removed_doc_ids: set[UUID],
) -> int:
    """Build or refresh the LanceDB vector index from processed chunk files.

    Returns:
        The total number of indexed vector rows after refresh.
    """
    lancedb_path = output_dir / "lancedb"
    store = LanceDBRetrievalStore(lancedb_path)
    embedder = IndexingEmbeddingProvider.from_settings()
    expected_source_chunk_count = sum(
        entry.section_chunk_count + entry.table_chunk_count for entry in state.filings.values()
    )
    if expected_source_chunk_count == 0:
        store.clear()
        store.save_metadata(
            {
                "index_schema_version": _INDEX_SCHEMA_VERSION,
                "embedding_model": "",
                "embedding_base_url": None,
                "source_chunk_count": 0,
                "vector_row_count": 0,
                "chunk_count": 0,
            }
        )
        logger.info("No chunks to index in LanceDB")
        return 0

    metadata = store.load_metadata()
    rebuild_all = (
        metadata is None
        or not store.has_table
        or metadata.get("embedding_model") != embedder.embedding_model
        or metadata.get("index_schema_version") != _INDEX_SCHEMA_VERSION
    )

    current_doc_ids = [UUID(doc_id) for doc_id in sorted(state.filings)]
    if rebuild_all:
        logger.info("Rebuilding LanceDB index from processed chunk files")
        store.clear()
        doc_ids_to_index = current_doc_ids
    else:
        for doc_id in sorted(removed_doc_ids, key=str):
            store.delete_doc(doc_id)
        for doc_id in sorted(refreshed_doc_ids, key=str):
            store.delete_doc(doc_id)
        doc_ids_to_index = sorted(refreshed_doc_ids, key=str)

    chunks_to_index = _load_chunks_from_disk(output_dir, doc_ids_to_index)
    if chunks_to_index:
        logger.info(
            "Refreshing LanceDB rows for %d filing(s), %d chunk(s)",
            len(doc_ids_to_index),
            len(chunks_to_index),
        )

    embedding_dimensions = int((metadata or {}).get("embedding_dimensions", 0)) if metadata else 0
    segmented_chunks = 0
    chunk_count = len(chunks_to_index)
    chunk_segments: list[tuple[SectionChunk | TableChunk, list]] = []
    batch_items: list[_ChunkSegmentBatchItem] = []

    for chunk in chunks_to_index:
        segments = segment_chunk_for_indexing(chunk)
        if len(segments) > 1:
            segmented_chunks += 1
            logger.debug(
                "Segmented %s chunk %s into %d rows (%d chars)",
                _chunk_kind_label(chunk),
                chunk.chunk_id,
                len(segments),
                len(chunk.text if isinstance(chunk, SectionChunk) else chunk.raw_text),
            )
        chunk_segments.append((chunk, segments))
        batch_items.extend(
            _ChunkSegmentBatchItem(chunk=chunk, segment_text=segment.text) for segment in segments
        )

    all_embeddings: list[list[float]] = []
    total_batches = max(1, (len(batch_items) + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE)
    for batch_index, start in enumerate(range(0, len(batch_items), _EMBED_BATCH_SIZE), start=1):
        batch = batch_items[start : start + _EMBED_BATCH_SIZE]
        batch_embeddings = _embed_segment_batch(
            embedder,
            batch,
            output_dir=output_dir,
        )
        if batch_embeddings and embedding_dimensions == 0:
            embedding_dimensions = len(batch_embeddings[0])
        all_embeddings.extend(batch_embeddings)
        if batch_index == 1 or batch_index == total_batches or batch_index % 10 == 0:
            logger.info(
                "Embedded batch %d/%d (%d segment rows)",
                batch_index,
                total_batches,
                len(batch),
            )

    total_vector_rows = sum(len(segments) for _, segments in chunk_segments)
    total_write_batches = (
        max(1, (total_vector_rows + _INDEX_WRITE_BATCH_SIZE - 1) // _INDEX_WRITE_BATCH_SIZE)
        if total_vector_rows > 0
        else 0
    )
    embedding_cursor = 0
    pending_rows: list[dict[str, object]] = []
    write_batch_index = 0
    write_phase_start = monotonic()

    def flush_pending_rows(*, chunk_index: int, vector_row_count: int) -> None:
        nonlocal pending_rows, write_batch_index
        if not pending_rows:
            return

        batch_rows = pending_rows
        pending_rows = []
        store.add_rows(batch_rows)
        write_batch_index += 1
        logger.info(
            "Indexed rows batch %d/%d (%d rows, chunks=%d/%d, vector rows=%d, elapsed=%.1fs)",
            write_batch_index,
            total_write_batches,
            len(batch_rows),
            chunk_index,
            chunk_count,
            vector_row_count,
            monotonic() - write_phase_start,
        )

    for index, (chunk, segments) in enumerate(chunk_segments, start=1):
        next_cursor = embedding_cursor + len(segments)
        chunk_rows = store.build_chunk_segment_rows(
            chunk,
            segments,
            all_embeddings[embedding_cursor:next_cursor],
        )
        embedding_cursor = next_cursor
        pending_rows.extend(chunk_rows)

        if index == 1 or index == chunk_count or index % _INDEX_PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Prepared chunk %d/%d (%0.1f%%, buffered rows=%d, vector rows=%d)",
                index,
                chunk_count,
                (index / chunk_count) * 100,
                len(pending_rows),
                embedding_cursor,
            )
        if len(pending_rows) >= _INDEX_WRITE_BATCH_SIZE:
            flush_pending_rows(chunk_index=index, vector_row_count=embedding_cursor)

    flush_pending_rows(chunk_index=chunk_count, vector_row_count=embedding_cursor)

    store.save_metadata(
        {
            "index_schema_version": _INDEX_SCHEMA_VERSION,
            "embedding_model": embedder.embedding_model,
            "embedding_base_url": embedder.base_url,
            "embedding_dimensions": embedding_dimensions,
            "source_chunk_count": expected_source_chunk_count,
            "vector_row_count": store.chunk_count,
            "chunk_count": store.chunk_count,
        }
    )

    logger.info(
        "LanceDB index built at %s: %d source chunks -> %d vector rows, segmented=%d, model=%s",
        lancedb_path,
        expected_source_chunk_count,
        store.chunk_count,
        segmented_chunks,
        embedder.embedding_model,
    )
    return store.chunk_count


def run_pipeline(
    raw_dir: Path = Path("data/raw"),
    output_dir: Path = Path("data/processed"),
    companyfacts_filename: str = "companyfacts.json",
    *,
    workers: int = 0,
) -> dict:
    """Run the full dual-track ingestion pipeline.

    Args:
        raw_dir: Path to ``data/raw/``.
        output_dir: Path to ``data/processed/``.
        companyfacts_filename: Name of the companyfacts JSON inside raw_dir.
        workers: Number of filing-level PDF ingestion workers to use.

    Returns:
        A summary dict with counts and gap information.
    """
    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)
    pipeline_start = monotonic()

    # 1. Build manifest.
    logger.info("Building filing manifest...")
    manifest = build_manifest(raw_dir)
    logger.info(print_manifest_summary(manifest))

    # 2. Resolve available filings to FilingDocument records.
    filings = resolve_all_filings(manifest)
    logger.info("Resolved %d filing documents", len(filings))

    # 3. Load reuse state and plan which filings still require parsing.
    state = load_ingestion_state(output_dir)
    current_doc_id_strings = {str(filing.doc_id) for filing in filings}
    stale_doc_ids = {UUID(doc_id) for doc_id in set(state.filings) - current_doc_id_strings}
    for doc_id in stale_doc_ids:
        remove_filing_artifacts(doc_id, output_dir)
        state.filings.pop(str(doc_id), None)

    filing_plans = _plan_filing_jobs(filings, raw_dir, output_dir, state)
    filings_to_process = [plan.filing for plan in filing_plans if not plan.reuse_existing]
    reused_filings = sum(1 for plan in filing_plans if plan.reuse_existing)

    if reused_filings:
        logger.info("Reusing %d unchanged filing(s)", reused_filings)
    for plan in filing_plans:
        if not plan.reuse_existing and plan.reason not in {"missing_source", "missing_state"}:
            logger.info(
                "Reprocessing %s due to %s",
                period_key_from_doc(plan.filing),
                plan.reason,
            )

    # 4. Normalize XBRL/companyfacts (moved before filing parsing so facts are
    #    available for reconciliation against extracted tables).
    companyfacts_path = raw_dir / companyfacts_filename
    facts: list[FactRecord] = []
    facts_reused = False
    if companyfacts_path.exists():
        facts_source_fingerprint = fingerprint_file(companyfacts_path)
        facts_state = state.facts
        can_reuse_facts = (
            facts_state is not None
            and facts_state.source_path == companyfacts_filename
            and facts_state.source_fingerprint == facts_source_fingerprint
            and facts_state.parser_fingerprint == _FACTS_PARSER_FINGERPRINT
            and _facts_artifact_exists(output_dir)
        )
        if can_reuse_facts:
            facts_reused = True
            logger.info("Reusing unchanged companyfacts output")
            # Load facts from disk so they're available for reconciliation.
            facts = _load_facts_from_disk(output_dir)
        else:
            logger.info("Normalizing companyfacts...")
            facts = normalize_companyfacts(companyfacts_path)
            logger.info(summarize_facts(facts))
            write_facts(facts, output_dir)
            state.facts = FactsStateEntry(
                source_path=companyfacts_filename,
                source_fingerprint=facts_source_fingerprint,
                parser_fingerprint=_FACTS_PARSER_FINGERPRINT,
                fact_record_count=len(facts),
            )
    else:
        logger.warning("companyfacts.json not found at %s", companyfacts_path)

    if facts_reused and state.facts is not None:
        fact_count = state.facts.fact_record_count
    else:
        fact_count = len(facts)

    # 5. Parse narrative chunks and extract tables only for active filings.
    worker_count = _resolve_worker_count(workers, len(filings_to_process))
    ingestion_diagnostics: dict[str, int] = {}
    try:
        all_section_chunks, all_table_chunks, failed_details, ingestion_diagnostics = (
            _run_filing_ingestion_jobs(
                filings_to_process,
                raw_dir,
                workers=worker_count,
            )
        )
    except (BrokenProcessPool, OSError, PermissionError) as exc:
        if worker_count <= 1:
            raise
        logger.warning(
            "Parallel filing ingestion unavailable (%s). Falling back to sequential mode.",
            exc,
        )
        worker_count = 1
        all_section_chunks, all_table_chunks, failed_details, ingestion_diagnostics = (
            _run_filing_ingestion_jobs(
                filings_to_process,
                raw_dir,
                workers=worker_count,
            )
        )

    parser_diagnostic_details = list(ingestion_diagnostics.pop("parser_diagnostic_details", []))

    # 6. Reconcile extracted tables against authoritative XBRL facts.
    if facts:
        known_doc_ids = {filing.doc_id for filing in filings}
        reused_doc_ids = [
            plan.filing.doc_id
            for plan in filing_plans
            if plan.reuse_existing and plan.filing.doc_id in known_doc_ids
        ]
        if reused_doc_ids:
            all_table_chunks.update(_load_tables_from_disk(output_dir, reused_doc_ids))
        all_table_chunks, reconciliation_mismatches = _reconcile_filing_tables(
            all_table_chunks, filings, facts
        )
        ingestion_diagnostics["fact_reconciliation_mismatches"] = reconciliation_mismatches
        if reconciliation_mismatches > 0:
            logger.warning(
                "%d table-vs-fact mismatch(es) found during reconciliation",
                reconciliation_mismatches,
            )

    # 7. Persist manifest and any reparsed filing outputs.
    write_manifest(manifest, output_dir)

    total_sections = 0
    total_tables = 0
    successful_reprocessed = 0
    failed_paths = {detail["source_path"] for detail in failed_details}
    result_by_doc_id = {
        doc_id: (
            all_section_chunks.get(doc_id, []),
            all_table_chunks.get(doc_id, []),
        )
        for doc_id in {plan.filing.doc_id for plan in filing_plans if not plan.reuse_existing}
    }

    for plan in filing_plans:
        doc_id = plan.filing.doc_id
        if plan.reuse_existing:
            existing_state = state.filings.get(str(doc_id))
            if existing_state is not None:
                total_sections += existing_state.section_chunk_count
                total_tables += existing_state.table_chunk_count
            if facts and doc_id in all_table_chunks:
                tables_dir = output_dir / "tables" / str(doc_id)
                if tables_dir.is_dir():
                    for table in all_table_chunks[doc_id]:
                        path = tables_dir / f"{table.chunk_id}.json"
                        path.write_text(table.model_dump_json(indent=2), encoding="utf-8")
            continue

        if str(plan.pdf_path) in failed_paths:
            continue

        sections, tables = result_by_doc_id.get(doc_id, ([], []))
        write_filing_bundle(plan.filing, sections, tables, output_dir)
        state.filings[str(doc_id)] = FilingStateEntry(
            doc_id=doc_id,
            source_path=plan.filing.source_path,
            source_fingerprint=plan.source_fingerprint or "",
            parser_fingerprint=_FILING_PARSER_FINGERPRINT,
            section_chunk_count=len(sections),
            table_chunk_count=len(tables),
        )
        total_sections += len(sections)
        total_tables += len(tables)
        successful_reprocessed += 1

    for plan in filing_plans:
        if plan.reuse_existing:
            continue
        if str(plan.pdf_path) in failed_paths:
            logger.warning(
                "Leaving prior artifacts untouched for failed filing %s",
                period_key_from_doc(plan.filing),
            )

    # 8. Build / refresh the LanceDB vector index.
    lancedb_path = output_dir / "lancedb"
    refreshed_doc_ids = {
        plan.filing.doc_id
        for plan in filing_plans
        if not plan.reuse_existing and str(plan.pdf_path) not in failed_paths
    }
    lancedb_chunks_indexed = _build_lancedb_index(
        output_dir,
        state,
        refreshed_doc_ids=refreshed_doc_ids,
        removed_doc_ids=stale_doc_ids,
    )
    save_ingestion_state(state, output_dir)

    # 9. Log warnings for validation / fallback issues.
    if ingestion_diagnostics.get("fallback_pages", 0) > 0:
        logger.warning(
            "Parser fallback was used on %d page(s) across all filings",
            ingestion_diagnostics["fallback_pages"],
        )
    if ingestion_diagnostics.get("failed_pages", 0) > 0:
        logger.warning(
            "%d page(s) had parser failures (no usable extraction)",
            ingestion_diagnostics["failed_pages"],
        )
    if ingestion_diagnostics.get("validation_failed_tables", 0) > 0:
        logger.warning(
            "%d table(s) have FAILED numeric validation — review before citing",
            ingestion_diagnostics["validation_failed_tables"],
        )
    if ingestion_diagnostics.get("validation_suspect_tables", 0) > 0:
        logger.warning(
            "%d table(s) have SUSPECT numeric cells (possible OCR issues)",
            ingestion_diagnostics["validation_suspect_tables"],
        )

    if ingestion_diagnostics.get("fact_reconciliation_mismatches", 0) > 0:
        logger.warning(
            "%d table-vs-fact reconciliation mismatch(es) detected",
            ingestion_diagnostics["fact_reconciliation_mismatches"],
        )

    # 10. Report coverage.
    summary = {
        "manifest_entries": manifest.total,
        "filings": len(filings),
        "section_chunks": total_sections,
        "table_chunks": total_tables,
        "fact_records": fact_count,
        "manifest_available": manifest.available_count,
        "manifest_gaps": manifest.gap_count,
        "reprocessed_filings": successful_reprocessed,
        "reused_filings": reused_filings,
        "failed_filings": len(failed_details),
        "gap_details": [
            {
                "fiscal_year": g.fiscal_year,
                "fiscal_quarter": g.fiscal_quarter,
                "filing_type": g.filing_type.value,
                "status": g.status.value,
                "notes": g.notes,
            }
            for g in manifest.gaps
        ],
        "failed_details": failed_details,
        "facts_reused": facts_reused,
        "ingestion_diagnostics": ingestion_diagnostics,
        "parser_diagnostic_details": parser_diagnostic_details,
        "lancedb_status": "ok",
        "lancedb_path": str(lancedb_path.resolve()),
        "lancedb_chunks_indexed": lancedb_chunks_indexed,
        "elapsed_seconds": round(monotonic() - pipeline_start, 2),
        "workers": worker_count,
    }

    logger.info(
        "Pipeline complete: %s",
        {
            k: v
            for k, v in summary.items()
            if k
            not in {
                "gap_details",
                "failed_details",
                "ingestion_diagnostics",
                "parser_diagnostic_details",
            }
        },
    )
    if summary["manifest_gaps"] > 0:
        logger.warning("Coverage gaps detected:")
        for gap in summary["gap_details"]:
            logger.warning("  %s", gap)
    if failed_details:
        logger.warning("Failed PDF ingestions detected:")
        for failure in failed_details:
            logger.warning("  %s", failure)
    if parser_diagnostic_details:
        logger.warning("Parser diagnostic details:")
        for detail in parser_diagnostic_details:
            logger.warning("  %s", detail)

    return summary
