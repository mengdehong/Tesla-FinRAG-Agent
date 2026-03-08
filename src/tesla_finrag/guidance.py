"""Shared processed-corpus readiness guidance.

Provides a centralized helper so every runtime surface – CLI, evaluation
runner, and Streamlit workbench – produces the same actionable remediation
message when ``data/processed`` is missing or malformed.
"""

from __future__ import annotations

from pathlib import Path

from tesla_finrag.runtime import (
    IncompatibleIndexError,
    MalformedProcessedArtifactError,
    MissingProcessedArtifactError,
    ProcessedCorpusError,
    load_processed_corpus,
)

# The single supported command operators should run to generate processed data.
INGEST_COMMAND = "uv run python -m tesla_finrag ingest"


def _missing_guidance(exc: MissingProcessedArtifactError) -> str:
    """Return remediation text for a missing artifact."""
    return (
        f"Processed corpus not ready: {exc}\n\n"
        "The runtime requires processed artifacts under data/processed/.\n"
        f"Run the ingestion pipeline first:\n\n  {INGEST_COMMAND}\n"
    )


def _malformed_guidance(exc: MalformedProcessedArtifactError) -> str:
    """Return remediation text for a malformed artifact."""
    return (
        f"Processed corpus invalid: {exc}\n\n"
        "The processed artifacts exist but cannot be loaded.\n"
        f"Regenerate them by re-running:\n\n  {INGEST_COMMAND}\n"
    )


def _incompatible_index_guidance(exc: IncompatibleIndexError) -> str:
    """Return remediation text for an incompatible LanceDB index."""
    return (
        f"LanceDB index incompatible: {exc}\n\n"
        "The persisted vector index was built with a different embedding model.\n"
        f"Rebuild it by re-running:\n\n  {INGEST_COMMAND}\n"
    )


def format_corpus_guidance(exc: ProcessedCorpusError) -> str:
    """Return a user-facing remediation message for any processed-corpus error.

    This is the single function all surfaces should call to produce consistent
    guidance text when runtime bootstrap fails.
    """
    if isinstance(exc, MissingProcessedArtifactError):
        return _missing_guidance(exc)
    if isinstance(exc, MalformedProcessedArtifactError):
        return _malformed_guidance(exc)
    if isinstance(exc, IncompatibleIndexError):
        return _incompatible_index_guidance(exc)
    # Generic fallback for unknown subclasses.
    return (
        f"Processed corpus error: {exc}\n\n"
        f"Try running:\n\n  {INGEST_COMMAND}\n"
    )


def check_corpus_readiness(
    processed_dir: str | Path | None = None,
) -> str | None:
    """Fully load the processed corpus and return guidance text on failure.

    Returns ``None`` when the corpus is ready, or a remediation string
    when it is not.
    """
    try:
        load_processed_corpus(processed_dir)
    except ProcessedCorpusError as exc:
        return format_corpus_guidance(exc)
    return None
