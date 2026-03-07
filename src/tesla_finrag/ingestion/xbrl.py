"""XBRL/companyfacts normalization into typed fact records.

Reads Tesla's ``companyfacts.json`` (downloaded from the SEC EDGAR API) and
normalises each numeric entry into a :class:`FactRecord` aligned by metric
name, unit, source filing, and period dates.  The resulting records are
suitable for downstream calculations without fragile PDF table parsing.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from uuid import UUID

from tesla_finrag.ingestion.source_adapter import _stable_doc_id
from tesla_finrag.models import FactRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TICKER = "TSLA"

# Fiscal-period token -> fiscal quarter mapping.
_FP_TO_QUARTER: dict[str, int | None] = {
    "FY": None,
    "Q1": 1,
    "Q2": 2,
    "Q3": 3,
    "Q4": None,  # Q4 data appears in the annual 10-K.
}

# Forms we ingest from.
_ACCEPTED_FORMS = {"10-K", "10-Q"}

# Minimum fiscal year to include.
_MIN_FY = 2021


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> date:
    """Parse an ISO-format date string from XBRL data."""
    return date.fromisoformat(s)


def _resolve_doc_id(
    fy: int,
    fp: str,
    form: str,
    doc_id_cache: dict[tuple[int, int | None, str], UUID],
) -> UUID:
    """Resolve the parent filing document ID from XBRL metadata.

    Uses the same deterministic UUID generation as the source adapter so
    that facts link correctly to their parent FilingDocument.
    """
    quarter = _FP_TO_QUARTER.get(fp)
    key = (fy, quarter, form)
    if key not in doc_id_cache:
        doc_id_cache[key] = _stable_doc_id(_DEFAULT_TICKER, form, fy, quarter)
    return doc_id_cache[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_companyfacts(
    companyfacts_path: Path,
    *,
    min_fy: int = _MIN_FY,
    namespaces: tuple[str, ...] = ("us-gaap", "dei"),
) -> list[FactRecord]:
    """Normalise Tesla companyfacts JSON into :class:`FactRecord` instances.

    Args:
        companyfacts_path: Path to ``companyfacts.json``.
        min_fy: Minimum fiscal year to include.
        namespaces: XBRL namespaces to process.

    Returns:
        A list of :class:`FactRecord` instances with aligned period
        metadata, metric identity, and source filing linkage.
    """
    with open(companyfacts_path) as f:
        data = json.load(f)

    all_facts = data.get("facts", {})
    records: list[FactRecord] = []
    doc_id_cache: dict[tuple[int, int | None, str], UUID] = {}
    skipped = 0

    for namespace in namespaces:
        ns_facts = all_facts.get(namespace, {})
        for concept_name, concept_data in ns_facts.items():
            label = concept_data.get("label") or concept_name
            units = concept_data.get("units", {})

            for unit_name, entries in units.items():
                for entry in entries:
                    fy = entry.get("fy")
                    fp = entry.get("fp", "")
                    form = entry.get("form", "")

                    # Filter: only accepted forms and relevant fiscal years.
                    if form not in _ACCEPTED_FORMS:
                        skipped += 1
                        continue
                    if fy is None or fy < min_fy:
                        skipped += 1
                        continue
                    if fp not in _FP_TO_QUARTER:
                        skipped += 1
                        continue

                    val = entry.get("val")
                    if val is None:
                        skipped += 1
                        continue

                    end_str = entry.get("end")
                    if not end_str:
                        skipped += 1
                        continue

                    period_end = _parse_date(end_str)
                    period_start = _parse_date(entry["start"]) if "start" in entry else None
                    is_instant = "start" not in entry

                    doc_id = _resolve_doc_id(fy, fp, form, doc_id_cache)

                    records.append(
                        FactRecord(
                            doc_id=doc_id,
                            concept=f"{namespace}:{concept_name}",
                            label=label,
                            value=float(val),
                            unit=unit_name,
                            scale=1,
                            period_start=period_start,
                            period_end=period_end,
                            is_instant=is_instant,
                        )
                    )

    logger.info(
        "Normalised %d fact records from %s (%d skipped)",
        len(records),
        companyfacts_path.name,
        skipped,
    )
    return records


def summarize_facts(records: list[FactRecord]) -> str:
    """Return a human-readable summary of normalised facts."""
    concepts = set()
    periods = set()
    units = set()
    for r in records:
        concepts.add(r.concept)
        periods.add(str(r.period_end))
        units.add(r.unit)

    lines = [
        "XBRL Facts Summary",
        f"  Total records:    {len(records)}",
        f"  Unique concepts:  {len(concepts)}",
        f"  Unique periods:   {len(periods)}",
        f"  Units:            {', '.join(sorted(units))}",
    ]
    return "\n".join(lines)
