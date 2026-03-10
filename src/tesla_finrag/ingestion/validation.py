"""Numeric validation and authoritative fact reconciliation for extracted tables.

Provides helpers that the ingestion pipeline runs over table chunks after
extraction so downstream consumers can distinguish trusted numeric evidence
from suspect or corrupted cells.

Key responsibilities:
- Normalize financial-style numeric strings (parentheses for negatives, commas,
  currency symbols, percentage signs, scale suffixes).
- Detect suspicious cells that look like OCR artefacts or garbled extraction.
- Reconcile table-derived values against authoritative XBRL facts when a
  concept-and-period match exists.
"""

from __future__ import annotations

import math
import re
from datetime import date

from tesla_finrag.models import (
    CellValidationResult,
    FactReconciliationResult,
    FactRecord,
    TableChunk,
    ValidationStatus,
)

# ---------------------------------------------------------------------------
# Numeric normalization
# ---------------------------------------------------------------------------

# Characters to strip before attempting numeric parse.
_STRIP_CHARS = "$€£¥ \t\u00a0\u2002\u2003"

# Pattern for parenthesized negative values: "(1,234)" -> -1234
_PAREN_NEG_RE = re.compile(r"^\((.+)\)$")

# Scale suffixes that appear after a number.
_SCALE_SUFFIXES: dict[str, float] = {
    "k": 1_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "t": 1_000_000_000_000,
}
_SCALE_RE = re.compile(
    r"([\d,.]+)\s*("
    + "|".join(re.escape(s) for s in sorted(_SCALE_SUFFIXES, key=len, reverse=True))
    + r")\s*$",
    re.IGNORECASE,
)

# Suspicious OCR substitution patterns.
_OCR_SUSPECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[Il][0-9]"),  # letter I/l mixed with digits
    re.compile(r"[0-9][Oo][0-9]"),  # letter O mixed with digits
    re.compile(r"[0-9][Ss][0-9]"),  # letter S mixed with digits
    re.compile(r"\b[A-Za-z]+\d+[A-Za-z]+\b"),  # mixed alpha-digit-alpha
]

# Broad pattern to detect if a cell is plausibly numeric.
_NUMERIC_CANDIDATE_RE = re.compile(r"^[\s$\u20ac\xa3\xa5()\-\u2212\u2013\u2014+,.\d%kKmMbBnNtT]+$")


def normalize_numeric_cell(raw: str) -> tuple[float | None, str]:
    """Attempt to parse a raw cell string as a numeric value.

    Returns:
        ``(value, detail)`` where *value* is the parsed float or ``None`` on
        failure, and *detail* describes the normalization path or failure reason.
    """
    if not raw or not raw.strip():
        return None, "empty"

    text = raw.strip()

    # Quick check: is this plausibly numeric at all?
    if not _NUMERIC_CANDIDATE_RE.match(text):
        return None, "non_numeric"

    # Handle percentage values.
    is_percent = "%" in text
    text = text.replace("%", "").strip()

    # Strip currency symbols and whitespace.
    text = text.strip(_STRIP_CHARS)

    if not text:
        return None, "empty_after_strip"

    # Detect dash/em-dash used for zero.
    if text in ("—", "–", "-", "−"):
        return 0.0, "dash_zero"

    # Handle accounting-style negatives: (1,234)
    neg = False
    m_paren = _PAREN_NEG_RE.match(text)
    if m_paren:
        neg = True
        text = m_paren.group(1).strip(_STRIP_CHARS)

    # Handle leading minus/en-dash/em-dash.
    if text and text[0] in "-−–":
        neg = True
        text = text[1:].strip()

    # Detect scale suffix.
    scale = 1.0
    m_scale = _SCALE_RE.match(text)
    if m_scale:
        text = m_scale.group(1)
        suffix = m_scale.group(2).lower()
        scale = _SCALE_SUFFIXES.get(suffix, 1.0)

    # Remove thousands separators.
    text = text.replace(",", "")

    if not text:
        return None, "empty_after_clean"

    try:
        value = float(text)
    except ValueError:
        return None, f"parse_failed: {raw!r}"

    if neg:
        value = -value
    value *= scale
    if is_percent:
        value /= 100.0

    if not math.isfinite(value):
        return None, "non_finite"

    detail = "ok"
    if scale != 1.0:
        detail = f"scaled_{scale:g}"
    if is_percent:
        detail = "percent"
    return value, detail


def is_numeric_candidate(cell: str) -> bool:
    """Return whether a cell looks like a plausible numeric value."""
    text = cell.strip()
    if not text:
        return False
    return bool(_NUMERIC_CANDIDATE_RE.match(text))


# ---------------------------------------------------------------------------
# Suspicious-cell detection
# ---------------------------------------------------------------------------


def detect_suspicious_cell(raw: str) -> str | None:
    """Return a description if *raw* shows signs of OCR corruption, else ``None``."""
    for pattern in _OCR_SUSPECT_PATTERNS:
        if pattern.search(raw):
            return f"OCR suspect: matches {pattern.pattern!r}"
    return None


# ---------------------------------------------------------------------------
# Table-level validation
# ---------------------------------------------------------------------------


def _has_significant_digits(cell: str) -> bool:
    """Return ``True`` if *cell* contains at least 2 digit characters.

    Used to distinguish OCR-corrupted numeric cells (e.g. ``51,6I8``) from
    ordinary text labels that happen to contain a digit (e.g. ``Item 1``).
    """
    return sum(c.isdigit() for c in cell) >= 2


def validate_table_cells(chunk: TableChunk) -> list[CellValidationResult]:
    """Validate all numeric-looking cells in a :class:`TableChunk`.

    Returns per-cell validation results for cells that appear numeric.
    Non-numeric text cells are also checked for OCR corruption if they
    contain significant digit content (>=2 digits).
    """
    results: list[CellValidationResult] = []

    for row_idx, row in enumerate(chunk.rows):
        for col_idx, cell_text in enumerate(row):
            if not is_numeric_candidate(cell_text):
                # Check non-candidate cells for OCR corruption if they
                # look like they should be numeric (have significant digits).
                if _has_significant_digits(cell_text):
                    suspicion = detect_suspicious_cell(cell_text)
                    if suspicion:
                        results.append(
                            CellValidationResult(
                                row_index=row_idx,
                                col_index=col_idx,
                                raw_value=cell_text,
                                normalized_value=None,
                                status=ValidationStatus.SUSPECT,
                                detail=suspicion,
                            )
                        )
                continue

            # Check for OCR suspicion.
            suspicion = detect_suspicious_cell(cell_text)
            value, detail = normalize_numeric_cell(cell_text)

            if suspicion:
                status = ValidationStatus.SUSPECT
                detail = suspicion
            elif value is None:
                status = ValidationStatus.FAILED
            else:
                status = ValidationStatus.VALID

            results.append(
                CellValidationResult(
                    row_index=row_idx,
                    col_index=col_idx,
                    raw_value=cell_text,
                    normalized_value=value,
                    status=status,
                    detail=detail,
                )
            )

    return results


def overall_validation_status(cell_results: list[CellValidationResult]) -> ValidationStatus:
    """Derive an overall table validation status from per-cell results."""
    if not cell_results:
        return ValidationStatus.NOT_CHECKED

    statuses = {r.status for r in cell_results}
    if ValidationStatus.FAILED in statuses:
        return ValidationStatus.FAILED
    if ValidationStatus.SUSPECT in statuses:
        return ValidationStatus.SUSPECT
    return ValidationStatus.VALID


# ---------------------------------------------------------------------------
# Authoritative fact reconciliation
# ---------------------------------------------------------------------------


def reconcile_table_with_facts(
    chunk: TableChunk,
    facts: list[FactRecord],
    *,
    period_end: date | None = None,
    tolerance: float = 0.01,
) -> list[FactReconciliationResult]:
    """Reconcile numeric cells in *chunk* against matching XBRL facts.

    Only reconciles when a cell's normalized value can be matched to a known
    fact by concept label (case-insensitive header match) and period.
    Concept matching is intentionally conservative: the table column header
    must contain the fact label or concept short name as a substring.

    Args:
        chunk: The table chunk to reconcile.
        facts: Authoritative XBRL fact records to compare against.
        period_end: If provided, restrict fact matching to this period.
        tolerance: Relative tolerance for numeric comparison.

    Returns:
        A list of :class:`FactReconciliationResult` for each matched cell.
    """
    if not facts or not chunk.headers or not chunk.rows:
        return []

    # Build a lookup from lowercase label fragments -> facts.
    label_to_facts: dict[str, list[FactRecord]] = {}
    for fact in facts:
        if period_end is not None and fact.period_end != period_end:
            continue
        if fact.doc_id != chunk.doc_id:
            continue
        # Index by short concept name (after the colon) and by label.
        short_name = fact.concept.split(":")[-1].lower()
        label = fact.label.lower()
        label_to_facts.setdefault(short_name, []).append(fact)
        if label != short_name:
            label_to_facts.setdefault(label, []).append(fact)

    # Map column indices to candidate fact keys.
    col_fact_keys: dict[int, list[str]] = {}
    for col_idx, header in enumerate(chunk.headers):
        header_lower = header.lower().strip()
        if not header_lower:
            continue
        header_singular = header_lower[:-1] if header_lower.endswith("s") else header_lower
        for key in label_to_facts:
            key_singular = key[:-1] if key.endswith("s") else key
            if (
                key in header_lower
                or key_singular in header_lower
                or header_singular == key
                or header_singular == key_singular
            ):
                col_fact_keys.setdefault(col_idx, []).append(key)

    if not col_fact_keys:
        return []

    results: list[FactReconciliationResult] = []
    for row in chunk.rows:
        for col_idx, keys in col_fact_keys.items():
            if col_idx >= len(row):
                continue
            value, _detail = normalize_numeric_cell(row[col_idx])
            if value is None:
                continue
            for key in keys:
                for fact in label_to_facts[key]:
                    fact_value = fact.value * fact.scale
                    if fact_value == 0 and value == 0:
                        matched = True
                    elif fact_value == 0:
                        matched = False
                    else:
                        matched = abs(value - fact_value) / abs(fact_value) <= tolerance
                    results.append(
                        FactReconciliationResult(
                            concept=fact.concept,
                            period_end=fact.period_end,
                            table_value=value,
                            fact_value=fact_value,
                            tolerance=tolerance,
                            matched=matched,
                            detail="match"
                            if matched
                            else f"mismatch: delta={value - fact_value:,.2f}",
                        )
                    )

    return results
