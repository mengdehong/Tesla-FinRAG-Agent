"""Source adapters for reconciling local raw files with filing metadata.

Each adapter takes a :class:`ManifestEntry` and returns the concrete
:class:`FilingDocument` metadata record that downstream parsers consume,
or ``None`` when the source is not available locally.
"""

from __future__ import annotations

from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

from tesla_finrag.models import (
    FilingAvailability,
    FilingDocument,
    FilingManifest,
    ManifestEntry,
)

# ---------------------------------------------------------------------------
# Deterministic doc_id generation
# ---------------------------------------------------------------------------

# Use a URL-style namespace so the same filing always gets the same UUID.
_DOC_ID_NAMESPACE = NAMESPACE_URL


def _stable_doc_id(
    ticker: str, filing_type: str, fiscal_year: int, fiscal_quarter: int | None
) -> UUID:
    """Generate a deterministic UUID for a filing based on its identity."""
    q_part = f"Q{fiscal_quarter}" if fiscal_quarter else "FY"
    url = f"tesla-finrag://{ticker}/{filing_type}/{fiscal_year}/{q_part}"
    return uuid5(_DOC_ID_NAMESPACE, url)


# ---------------------------------------------------------------------------
# Filing date heuristics
# ---------------------------------------------------------------------------

# Tesla typical filing dates (approximate; exact dates vary each year).
# 10-K: filed in late January / early February for prior FY.
# 10-Q Q1: filed in late April.
# 10-Q Q2: filed in late July.
# 10-Q Q3: filed in late October.
_TYPICAL_FILING_OFFSETS: dict[int | None, tuple[int, int]] = {
    None: (2, 1),  # 10-K: ~Feb 1 of next year
    1: (4, 25),  # Q1: ~Apr 25
    2: (7, 25),  # Q2: ~Jul 25
    3: (10, 25),  # Q3: ~Oct 25
}


def _estimate_filed_date(fiscal_year: int, fiscal_quarter: int | None) -> date:
    """Estimate the filing date for a Tesla SEC filing.

    This is a heuristic — actual filed dates come from EDGAR metadata.
    Used as a fallback when we have no accession number.
    """
    month, day = _TYPICAL_FILING_OFFSETS[fiscal_quarter]
    if fiscal_quarter is None:
        # 10-K for FY20XX is filed in early next year
        return date(fiscal_year + 1, month, day)
    return date(fiscal_year, month, day)


# ---------------------------------------------------------------------------
# Local source adapter
# ---------------------------------------------------------------------------


def resolve_filing_document(
    entry: ManifestEntry,
) -> FilingDocument | None:
    """Convert a manifest entry to a :class:`FilingDocument` if locally available.

    Returns ``None`` when the entry status is not AVAILABLE or has no
    ``source_path``.
    """
    if entry.status != FilingAvailability.AVAILABLE or entry.source_path is None:
        return None

    doc_id = _stable_doc_id(
        entry.ticker, entry.filing_type.value, entry.fiscal_year, entry.fiscal_quarter
    )

    return FilingDocument(
        doc_id=doc_id,
        ticker=entry.ticker,
        filing_type=entry.filing_type,
        period_end=entry.period_end,
        fiscal_year=entry.fiscal_year,
        fiscal_quarter=entry.fiscal_quarter,
        accession_number="",  # populated when EDGAR metadata is available
        filed_at=_estimate_filed_date(entry.fiscal_year, entry.fiscal_quarter),
        source_path=entry.source_path,
    )


def resolve_all_filings(manifest: FilingManifest) -> list[FilingDocument]:
    """Resolve all available manifest entries into :class:`FilingDocument` records.

    Entries that are not locally available are skipped.  The caller can
    compare the result length against ``manifest.available_count`` to
    verify full coverage.
    """
    docs: list[FilingDocument] = []
    for entry in manifest.entries:
        doc = resolve_filing_document(entry)
        if doc is not None:
            docs.append(doc)
    return docs


def period_key(fiscal_year: int, fiscal_quarter: int | None) -> str:
    """Build a canonical period key string.

    Examples: ``"FY2023"``, ``"Q1-2023"``.
    """
    if fiscal_quarter is None:
        return f"FY{fiscal_year}"
    return f"Q{fiscal_quarter}-{fiscal_year}"


def period_key_from_doc(doc: FilingDocument) -> str:
    """Extract the period key from a :class:`FilingDocument`."""
    return period_key(doc.fiscal_year, doc.fiscal_quarter)
