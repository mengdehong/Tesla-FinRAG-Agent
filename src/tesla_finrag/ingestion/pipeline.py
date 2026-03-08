"""End-to-end ingestion pipeline runner.

Orchestrates the full dual-track ingestion from ``data/raw/`` to
``data/processed/``, producing the normalised corpus and reporting
coverage gaps explicitly.
"""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import UUID

from tesla_finrag.ingestion.manifest import build_manifest, print_manifest_summary
from tesla_finrag.ingestion.narrative import parse_narrative
from tesla_finrag.ingestion.source_adapter import period_key_from_doc, resolve_all_filings
from tesla_finrag.ingestion.tables import extract_tables
from tesla_finrag.ingestion.writers import write_all
from tesla_finrag.ingestion.xbrl import normalize_companyfacts, summarize_facts
from tesla_finrag.logging_config import get_logger, suppress_pdfminer_font_warnings
from tesla_finrag.models import FilingDocument, SectionChunk, TableChunk

logger = get_logger(__name__)


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
        sections = parse_narrative(pdf_path, filing.doc_id)
        tables = extract_tables(pdf_path, filing.doc_id)
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


def run_pipeline(
    raw_dir: Path = Path("data/raw"),
    output_dir: Path = Path("data/processed"),
    companyfacts_filename: str = "companyfacts.json",
    *,
    workers: int = 1,
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

    # 3. Parse narrative chunks and extract tables from each filing PDF.
    worker_count = max(1, workers)
    try:
        all_section_chunks, all_table_chunks, failed_details = _run_filing_ingestion_jobs(
            filings,
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
            filings,
            raw_dir,
            workers=worker_count,
        )

    # 4. Normalize XBRL/companyfacts.
    companyfacts_path = raw_dir / companyfacts_filename
    facts = []
    if companyfacts_path.exists():
        logger.info("Normalizing companyfacts...")
        facts = normalize_companyfacts(companyfacts_path)
        logger.info(summarize_facts(facts))
    else:
        logger.warning("companyfacts.json not found at %s", companyfacts_path)

    # 5. Write all outputs.
    logger.info("Writing normalized outputs to %s...", output_dir)
    counts = write_all(manifest, filings, all_section_chunks, all_table_chunks, facts, output_dir)

    # 6. Report coverage.
    summary = {
        **counts,
        "manifest_available": manifest.available_count,
        "manifest_gaps": manifest.gap_count,
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
