"""Ingestion state tracking and fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_STATE_FILENAME = ".ingestion-state.json"


class FilingStateEntry(BaseModel):
    """Persisted state for a successfully written filing."""

    doc_id: UUID
    source_path: str
    source_fingerprint: str
    parser_fingerprint: str
    section_chunk_count: int = 0
    table_chunk_count: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FactsStateEntry(BaseModel):
    """Persisted state for normalized companyfacts output."""

    source_path: str
    source_fingerprint: str
    parser_fingerprint: str
    fact_record_count: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IngestionState(BaseModel):
    """Persisted incremental-ingestion state."""

    filings: dict[str, FilingStateEntry] = Field(default_factory=dict)
    facts: FactsStateEntry | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def state_path_for(output_dir: Path) -> Path:
    """Return the default state file path inside a processed corpus."""
    return output_dir / _STATE_FILENAME


def load_ingestion_state(output_dir: Path) -> IngestionState:
    """Load state if present; otherwise return an empty state object."""
    state_path = state_path_for(output_dir)
    if not state_path.exists():
        return IngestionState()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return IngestionState.model_validate(data)
    except Exception as exc:
        logger.warning("Ignoring invalid ingestion state at %s: %s", state_path, exc)
        return IngestionState()


def save_ingestion_state(state: IngestionState, output_dir: Path) -> Path:
    """Persist ingestion state to the processed corpus root."""
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_path_for(output_dir)
    payload = state.model_copy(update={"updated_at": datetime.now(UTC)})
    state_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    return state_path


def fingerprint_file(path: Path) -> str:
    """Compute a stable content fingerprint for a source file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_modules(module_paths: Iterable[Path], *, version_tag: str) -> str:
    """Hash parser-relevant source files into a reusable fingerprint."""
    digest = hashlib.sha256()
    digest.update(version_tag.encode("utf-8"))
    for module_path in sorted({path.resolve() for path in module_paths}):
        digest.update(module_path.name.encode("utf-8"))
        digest.update(module_path.read_bytes())
    return digest.hexdigest()
