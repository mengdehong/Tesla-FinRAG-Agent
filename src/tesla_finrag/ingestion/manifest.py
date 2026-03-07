"""Filing manifest builder and source inventory.

Enumerates the target Tesla SEC filing set and records which documents are
available locally, potentially downloadable from SEC EDGAR, or missing
entirely.  Gaps are surfaced explicitly so downstream evaluation knows
whether absent evidence reflects an ingestion failure or incomplete source
coverage.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from tesla_finrag.models import (
    FilingAvailability,
    FilingManifest,
    FilingType,
    ManifestEntry,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TICKER = "TSLA"

# Target fiscal years covered by the corpus.
_TARGET_YEARS = range(2021, 2026)

# Map fiscal quarter -> period-end month/day (Tesla fiscal year = calendar year).
_QUARTER_PERIOD_ENDS: dict[int | None, tuple[int, int]] = {
    None: (12, 31),  # 10-K annual
    1: (3, 31),
    2: (6, 30),
    3: (9, 30),
}

# Filename pattern for local raw PDFs.
# Examples: Tesla_2021_全年_10-K.pdf, Tesla_2021_Q1_10-Q.pdf
_PDF_FILENAME_RE = re.compile(
    r"^Tesla_(?P<year>\d{4})_(?P<period>全年|Q[1-3])_(?P<form>10-[KQ])\.pdf$"
)

# Map filename period token -> fiscal quarter.
_PERIOD_TOKEN_TO_QUARTER: dict[str, int | None] = {
    "全年": None,
    "Q1": 1,
    "Q2": 2,
    "Q3": 3,
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _period_end_for(fiscal_year: int, fiscal_quarter: int | None) -> date:
    """Return the period-end date for a given year and quarter."""
    month, day = _QUARTER_PERIOD_ENDS[fiscal_quarter]
    return date(fiscal_year, month, day)


def _expected_filename(
    fiscal_year: int, fiscal_quarter: int | None, filing_type: FilingType
) -> str:
    """Build the conventional raw PDF filename."""
    if fiscal_quarter is None:
        period_token = "全年"
    else:
        period_token = f"Q{fiscal_quarter}"
    return f"Tesla_{fiscal_year}_{period_token}_{filing_type.value}.pdf"


# ---------------------------------------------------------------------------
# Target set generation
# ---------------------------------------------------------------------------


def _build_target_entries(
    years: range = _TARGET_YEARS,
) -> list[ManifestEntry]:
    """Return the full target filing set with status = MISSING."""
    entries: list[ManifestEntry] = []
    for year in years:
        # Annual 10-K
        entries.append(
            ManifestEntry(
                ticker=_DEFAULT_TICKER,
                filing_type=FilingType.ANNUAL,
                fiscal_year=year,
                fiscal_quarter=None,
                period_end=_period_end_for(year, None),
            )
        )
        # Quarterly 10-Q (Q1-Q3; Q4 is covered by the annual 10-K)
        for q in (1, 2, 3):
            entries.append(
                ManifestEntry(
                    ticker=_DEFAULT_TICKER,
                    filing_type=FilingType.QUARTERLY,
                    fiscal_year=year,
                    fiscal_quarter=q,
                    period_end=_period_end_for(year, q),
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Local source scanning
# ---------------------------------------------------------------------------


def scan_local_sources(raw_dir: Path) -> dict[tuple[int, int | None, str], str]:
    """Scan ``raw_dir`` for Tesla PDF filings.

    Returns a mapping of ``(year, quarter, form)`` -> relative path
    for every file matching the naming convention.
    """
    inventory: dict[tuple[int, int | None, str], str] = {}
    if not raw_dir.is_dir():
        return inventory
    for path in sorted(raw_dir.iterdir()):
        m = _PDF_FILENAME_RE.match(path.name)
        if m is None:
            continue
        year = int(m.group("year"))
        quarter = _PERIOD_TOKEN_TO_QUARTER[m.group("period")]
        form = m.group("form")
        inventory[(year, quarter, form)] = str(path.relative_to(raw_dir.parent.parent))
        # relative to repo root: data/raw/...
    return inventory


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    raw_dir: Path,
    *,
    years: range = _TARGET_YEARS,
) -> FilingManifest:
    """Build a :class:`FilingManifest` by reconciling targets with local sources.

    Args:
        raw_dir: Path to ``data/raw/`` directory.
        years: Range of fiscal years to target.

    Returns:
        A :class:`FilingManifest` with each entry marked as *available*,
        *downloadable* (target exists but no local file), or *missing*.
    """
    local = scan_local_sources(raw_dir)
    targets = _build_target_entries(years)

    resolved: list[ManifestEntry] = []
    for entry in targets:
        key = (entry.fiscal_year, entry.fiscal_quarter, entry.filing_type.value)
        if key in local:
            resolved.append(
                entry.model_copy(
                    update={
                        "status": FilingAvailability.AVAILABLE,
                        "source_path": local[key],
                    }
                )
            )
        else:
            # Mark as downloadable (SEC EDGAR is a known source) but not local.
            resolved.append(
                entry.model_copy(
                    update={
                        "status": FilingAvailability.DOWNLOADABLE,
                        "notes": "Not found locally; may be downloadable from SEC EDGAR.",
                    }
                )
            )

    return FilingManifest(entries=resolved)


def print_manifest_summary(manifest: FilingManifest) -> str:
    """Return a human-readable summary of the manifest."""
    lines = [
        f"Filing Manifest — {manifest.total} target filings",
        f"  Available:    {manifest.available_count}",
        f"  Gaps:         {manifest.gap_count}",
        "",
    ]
    if manifest.gaps:
        lines.append("Gaps:")
        for gap in manifest.gaps:
            q_label = f"Q{gap.fiscal_quarter}" if gap.fiscal_quarter else "FY"
            lines.append(
                f"  {gap.ticker} {gap.filing_type.value} "
                f"{gap.fiscal_year} {q_label} — {gap.status.value}: {gap.notes}"
            )
    return "\n".join(lines)
