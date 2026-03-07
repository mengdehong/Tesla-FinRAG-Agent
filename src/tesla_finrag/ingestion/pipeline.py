"""End-to-end ingestion pipeline runner.

Orchestrates the full dual-track ingestion from ``data/raw/`` to
``data/processed/``, producing the normalised corpus and reporting
coverage gaps explicitly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from tesla_finrag.ingestion.manifest import build_manifest, print_manifest_summary
from tesla_finrag.ingestion.narrative import parse_narrative
from tesla_finrag.ingestion.source_adapter import period_key_from_doc, resolve_all_filings
from tesla_finrag.ingestion.tables import extract_tables
from tesla_finrag.ingestion.writers import write_all
from tesla_finrag.ingestion.xbrl import normalize_companyfacts, summarize_facts
from tesla_finrag.models import SectionChunk, TableChunk

logger = logging.getLogger(__name__)


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


def run_pipeline(
    raw_dir: Path = Path("data/raw"),
    output_dir: Path = Path("data/processed"),
    companyfacts_filename: str = "companyfacts.json",
) -> dict:
    """Run the full dual-track ingestion pipeline.

    Args:
        raw_dir: Path to ``data/raw/``.
        output_dir: Path to ``data/processed/``.
        companyfacts_filename: Name of the companyfacts JSON inside raw_dir.

    Returns:
        A summary dict with counts and gap information.
    """
    # 1. Build manifest.
    logger.info("Building filing manifest...")
    manifest = build_manifest(raw_dir)
    logger.info(print_manifest_summary(manifest))

    # 2. Resolve available filings to FilingDocument records.
    filings = resolve_all_filings(manifest)
    logger.info("Resolved %d filing documents", len(filings))

    # 3. Parse narrative chunks and extract tables from each filing PDF.
    all_section_chunks: dict[UUID, list[SectionChunk]] = {}
    all_table_chunks: dict[UUID, list[TableChunk]] = {}

    for filing in filings:
        pdf_path = _resolve_source_pdf_path(raw_dir, filing.source_path)
        if pdf_path is None:
            logger.warning(
                "PDF not found for %s: %s", period_key_from_doc(filing), filing.source_path
            )
            continue

        pk = period_key_from_doc(filing)
        logger.info("Parsing %s (%s)...", pk, filing.source_path)

        try:
            sections = parse_narrative(pdf_path, filing.doc_id)
            all_section_chunks[filing.doc_id] = sections
            logger.info("  %d narrative chunks", len(sections))

            tables = extract_tables(pdf_path, filing.doc_id)
            all_table_chunks[filing.doc_id] = tables
            logger.info("  %d table chunks", len(tables))
        except Exception:
            logger.exception("Failed to ingest %s from %s", pk, pdf_path)
            all_section_chunks.setdefault(filing.doc_id, [])
            all_table_chunks.setdefault(filing.doc_id, [])

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
    }

    logger.info("Pipeline complete: %s", {k: v for k, v in summary.items() if k != "gap_details"})
    if summary["manifest_gaps"] > 0:
        logger.warning("Coverage gaps detected:")
        for gap in summary["gap_details"]:
            logger.warning("  %s", gap)

    return summary
