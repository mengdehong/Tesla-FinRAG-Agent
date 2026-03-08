"""Processed data writers for normalized ingestion outputs.

All derived artifacts are written outside ``data/raw/`` into
``data/processed/`` to preserve raw inputs as immutable source data.

Output layout::

    data/processed/
    ├── manifest.json              # Filing manifest with gap reporting
    ├── filings/                   # One JSON per resolved FilingDocument
    │   └── <doc_id>.json
    ├── chunks/                    # Narrative section chunks
    │   └── <doc_id>/
    │       └── <chunk_id>.json
    ├── tables/                    # Extracted table chunks
    │   └── <doc_id>/
    │       └── <chunk_id>.json
    └── facts/                     # XBRL/companyfacts fact records
        └── all_facts.jsonl        # One JSON object per line
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from tesla_finrag.models import (
    FactRecord,
    FilingDocument,
    FilingManifest,
    SectionChunk,
    TableChunk,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = Path("data/processed")


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------


def _serialize(obj: object) -> object:
    """Custom serializer for types not handled by stdlib json."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _model_to_dict(model: object) -> dict:
    """Convert a Pydantic model to a JSON-compatible dict."""
    return json.loads(model.model_dump_json())  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_manifest(
    manifest: FilingManifest,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write the filing manifest to ``data/processed/manifest.json``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "manifest.json"
    data = _model_to_dict(manifest)
    path.write_text(json.dumps(data, indent=2, default=_serialize), encoding="utf-8")
    logger.info("Wrote manifest to %s (%d entries)", path, manifest.total)
    return path


def write_filings(
    filings: list[FilingDocument],
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    """Write each resolved filing document to ``data/processed/filings/``."""
    filings_dir = output_dir / "filings"
    filings_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for filing in filings:
        path = filings_dir / f"{filing.doc_id}.json"
        path.write_text(
            json.dumps(_model_to_dict(filing), indent=2, default=_serialize),
            encoding="utf-8",
        )
        paths.append(path)
    logger.info("Wrote %d filing documents to %s", len(filings), filings_dir)
    return paths


def write_section_chunks(
    chunks: list[SectionChunk],
    doc_id: UUID,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    """Write narrative section chunks to ``data/processed/chunks/<doc_id>/``."""
    chunks_dir = output_dir / "chunks" / str(doc_id)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for chunk in chunks:
        path = chunks_dir / f"{chunk.chunk_id}.json"
        path.write_text(
            json.dumps(_model_to_dict(chunk), indent=2, default=_serialize),
            encoding="utf-8",
        )
        paths.append(path)
    logger.info("Wrote %d section chunks for doc %s", len(chunks), doc_id)
    return paths


def write_table_chunks(
    chunks: list[TableChunk],
    doc_id: UUID,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    """Write table chunks to ``data/processed/tables/<doc_id>/``."""
    tables_dir = output_dir / "tables" / str(doc_id)
    tables_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for chunk in chunks:
        path = tables_dir / f"{chunk.chunk_id}.json"
        path.write_text(
            json.dumps(_model_to_dict(chunk), indent=2, default=_serialize),
            encoding="utf-8",
        )
        paths.append(path)
    logger.info("Wrote %d table chunks for doc %s", len(chunks), doc_id)
    return paths


def clear_filing_outputs(
    doc_id: UUID,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> None:
    """Remove derived chunk artifacts for a single filing before rewrite."""
    for subdir in ("chunks", "tables"):
        path = output_dir / subdir / str(doc_id)
        if path.exists():
            shutil.rmtree(path)


def remove_filing_artifacts(
    doc_id: UUID,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> None:
    """Remove all persisted artifacts for a filing."""
    filing_path = output_dir / "filings" / f"{doc_id}.json"
    if filing_path.exists():
        filing_path.unlink()
    clear_filing_outputs(doc_id, output_dir)


def write_filing_bundle(
    filing: FilingDocument,
    section_chunks: list[SectionChunk],
    table_chunks: list[TableChunk],
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> dict[str, int]:
    """Atomically replace persisted outputs for a single filing."""
    write_filings([filing], output_dir)
    clear_filing_outputs(filing.doc_id, output_dir)
    write_section_chunks(section_chunks, filing.doc_id, output_dir)
    write_table_chunks(table_chunks, filing.doc_id, output_dir)
    return {
        "filings": 1,
        "section_chunks": len(section_chunks),
        "table_chunks": len(table_chunks),
    }


def write_facts(
    facts: list[FactRecord],
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write all fact records to ``data/processed/facts/all_facts.jsonl``."""
    facts_dir = output_dir / "facts"
    facts_dir.mkdir(parents=True, exist_ok=True)
    path = facts_dir / "all_facts.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for fact in facts:
            f.write(json.dumps(_model_to_dict(fact), default=_serialize) + "\n")
    logger.info("Wrote %d fact records to %s", len(facts), path)
    return path


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def write_all(
    manifest: FilingManifest,
    filings: list[FilingDocument],
    section_chunks: dict[UUID, list[SectionChunk]],
    table_chunks: dict[UUID, list[TableChunk]],
    facts: list[FactRecord],
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> dict[str, int]:
    """Write all normalized outputs to ``data/processed/``.

    Args:
        manifest: The filing manifest.
        filings: Resolved filing documents.
        section_chunks: Mapping of doc_id -> list of narrative chunks.
        table_chunks: Mapping of doc_id -> list of table chunks.
        facts: All XBRL fact records.
        output_dir: Root output directory.

    Returns:
        A summary dict with counts of each artifact type written.
    """
    write_manifest(manifest, output_dir)
    write_filings(filings, output_dir)

    total_sections = 0
    for doc_id, chunks in section_chunks.items():
        write_section_chunks(chunks, doc_id, output_dir)
        total_sections += len(chunks)

    total_tables = 0
    for doc_id, chunks in table_chunks.items():
        write_table_chunks(chunks, doc_id, output_dir)
        total_tables += len(chunks)

    write_facts(facts, output_dir)

    summary = {
        "manifest_entries": manifest.total,
        "filings": len(filings),
        "section_chunks": total_sections,
        "table_chunks": total_tables,
        "fact_records": len(facts),
    }
    logger.info("Write complete: %s", summary)
    return summary
