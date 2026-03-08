"""End-to-end ingestion pipeline runner.

Orchestrates the full dual-track ingestion from ``data/raw/`` to
``data/processed/``, producing the normalised corpus and reporting
coverage gaps explicitly.
"""

from __future__ import annotations

import json as jsonlib
import os
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import UUID

from tesla_finrag.ingestion.analysis import analyze_filing_pdf
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
from tesla_finrag.ingestion.writers import (
    remove_filing_artifacts,
    write_facts,
    write_filing_bundle,
    write_manifest,
)
from tesla_finrag.ingestion.xbrl import normalize_companyfacts, summarize_facts
from tesla_finrag.logging_config import get_logger, suppress_pdfminer_font_warnings
from tesla_finrag.models import FilingDocument, SectionChunk, TableChunk
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
        return FilingIngestionResult(
            index=index,
            doc_id=filing.doc_id,
            period_key=period_key_from_doc(filing),
            source_path=str(pdf_path),
            section_chunks=[chunk.model_dump(mode="json") for chunk in sections],
            table_chunks=[chunk.model_dump(mode="json") for chunk in tables],
            elapsed_seconds=monotonic() - start,
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
        logger.error(
            "%s failed in %.1fs: %s", prefix, result.elapsed_seconds, result.error
        )
        return

    logger.info(
        "%s done in %.1fs: %d narrative chunks, %d table chunks",
        prefix,
        result.elapsed_seconds,
        len(result.section_chunks),
        len(result.table_chunks),
    )


def _run_filing_ingestion_jobs(
    filings: list[FilingDocument],
    raw_dir: Path,
    *,
    workers: int,
) -> tuple[dict[UUID, list[SectionChunk]], dict[UUID, list[TableChunk]], list[dict]]:
    """Run filing-level PDF ingestion sequentially or in parallel."""
    total = len(filings)
    all_section_chunks: dict[UUID, list[SectionChunk]] = {}
    all_table_chunks: dict[UUID, list[TableChunk]] = {}
    failed_details: list[dict] = []

    if total == 0:
        return all_section_chunks, all_table_chunks, failed_details

    logger.info("Processing %d filings with %d worker(s)...", total, workers)

    def consume(result: FilingIngestionResult) -> None:
        _log_filing_result(result, total)
        all_section_chunks[result.doc_id] = [
            SectionChunk.model_validate(chunk) for chunk in result.section_chunks
        ]
        all_table_chunks[result.doc_id] = [
            TableChunk.model_validate(chunk) for chunk in result.table_chunks
        ]
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
        return all_section_chunks, all_table_chunks, failed_details

    futures: dict[Future[FilingIngestionResult], FilingDocument] = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
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
    return all_section_chunks, all_table_chunks, failed_details


# ---------------------------------------------------------------------------
# LanceDB index builder
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 64


def _chunk_display_text(chunk: SectionChunk | TableChunk) -> str:
    """Extract the text used for embedding from a chunk."""
    if isinstance(chunk, SectionChunk):
        return chunk.text
    return chunk.raw_text


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
        The total number of indexed chunks after refresh.
    """
    lancedb_path = output_dir / "lancedb"
    store = LanceDBRetrievalStore(lancedb_path)
    embedder = IndexingEmbeddingProvider.from_settings()
    expected_chunk_count = sum(
        entry.section_chunk_count + entry.table_chunk_count
        for entry in state.filings.values()
    )
    if expected_chunk_count == 0:
        store.clear()
        store.save_metadata(
            {
                "embedding_model": "",
                "embedding_base_url": None,
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

    texts = [_chunk_display_text(chunk) for chunk in chunks_to_index]
    all_embeddings: list[list[float]] = []
    for index in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[index : index + _EMBED_BATCH_SIZE]
        all_embeddings.extend(embedder.embed_texts(batch))
        logger.info(
            "Embedded batch %d/%d (%d chunks)",
            index // _EMBED_BATCH_SIZE + 1,
            (len(texts) + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE,
            len(batch),
        )

    for chunk, embedding in zip(chunks_to_index, all_embeddings):
        if isinstance(chunk, SectionChunk):
            store.index_section_chunk(chunk, embedding)
        else:
            store.index_table_chunk(chunk, embedding)

    store.save_metadata(
        {
            "embedding_model": embedder.embedding_model,
            "embedding_base_url": embedder.base_url,
            "embedding_dimensions": (
                len(all_embeddings[0])
                if all_embeddings
                else (metadata or {}).get("embedding_dimensions", 0)
            ),
            "chunk_count": store.chunk_count,
        }
    )

    logger.info(
        "LanceDB index built at %s: %d chunks, model=%s",
        lancedb_path,
        store.chunk_count,
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
    stale_doc_ids = {
        UUID(doc_id) for doc_id in set(state.filings) - current_doc_id_strings
    }
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

    # 4. Parse narrative chunks and extract tables only for active filings.
    worker_count = _resolve_worker_count(workers, len(filings_to_process))
    try:
        all_section_chunks, all_table_chunks, failed_details = _run_filing_ingestion_jobs(
            filings_to_process,
            raw_dir,
            workers=worker_count,
        )
    except (OSError, PermissionError) as exc:
        if worker_count <= 1:
            raise
        logger.warning(
            "Parallel filing ingestion unavailable (%s). Falling back to sequential mode.",
            exc,
        )
        worker_count = 1
        all_section_chunks, all_table_chunks, failed_details = _run_filing_ingestion_jobs(
            filings_to_process,
            raw_dir,
            workers=worker_count,
        )

    # 5. Persist manifest and any reparsed filing outputs.
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

    # 6. Normalize XBRL/companyfacts with reuse checks.
    companyfacts_path = raw_dir / companyfacts_filename
    facts = []
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

    # 7. Build / refresh the LanceDB vector index.
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

    # 8. Report coverage.
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
        "lancedb_status": "ok",
        "lancedb_path": str(lancedb_path.resolve()),
        "lancedb_chunks_indexed": lancedb_chunks_indexed,
        "elapsed_seconds": round(monotonic() - pipeline_start, 2),
        "workers": worker_count,
    }

    logger.info(
        "Pipeline complete: %s",
        {k: v for k, v in summary.items() if k not in {"gap_details", "failed_details"}},
    )
    if summary["manifest_gaps"] > 0:
        logger.warning("Coverage gaps detected:")
        for gap in summary["gap_details"]:
            logger.warning("  %s", gap)
    if failed_details:
        logger.warning("Failed PDF ingestions detected:")
        for failure in failed_details:
            logger.warning("  %s", failure)

    return summary
