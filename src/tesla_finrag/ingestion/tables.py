"""Table extraction and normalization from Tesla SEC filing PDFs.

Each financial table is emitted as an independent :class:`TableChunk` with
structured metadata (headers, rows) and a serialised fallback ``raw_text``
suitable for embedding.  Tables are not merged into surrounding narrative
text.

Parser provenance and per-cell validation metadata are attached to each
chunk so downstream consumers can distinguish trusted evidence from
suspect or corrupted extractions.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from tesla_finrag.ingestion.analysis import FilingPdfAnalysis, analyze_filing_pdf
from tesla_finrag.ingestion.validation import (
    overall_validation_status,
    validate_table_cells,
)
from tesla_finrag.models import ChunkKind, ParserProvenance, TableChunk

# ---------------------------------------------------------------------------
# Section context detection
# ---------------------------------------------------------------------------

_ITEM_RE = re.compile(
    r"^(?:ITEM|Item)\s+(\d+[A-Z]?)[\.\:\s]?\s*(.*)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Table cleaning helpers
# ---------------------------------------------------------------------------


def _clean_cell(cell: str | None) -> str:
    """Normalize a single table cell value."""
    if cell is None:
        return ""
    # Collapse internal whitespace and newlines.
    return re.sub(r"\s+", " ", str(cell)).strip()


def _clean_table(raw_table: list[list[str | None]]) -> tuple[list[str], list[list[str]]]:
    """Clean a raw pdfplumber table into (headers, rows).

    Heuristic: the first non-empty row is treated as headers.
    Completely empty rows are dropped.
    """
    cleaned: list[list[str]] = []
    for raw_row in raw_table:
        row = [_clean_cell(c) for c in raw_row]
        # Drop rows that are entirely empty.
        if any(cell for cell in row):
            cleaned.append(row)

    if not cleaned:
        return [], []

    headers = cleaned[0]
    rows = cleaned[1:]
    return headers, rows


def _table_to_text(headers: list[str], rows: list[list[str]]) -> str:
    """Serialize a table into a pipe-delimited text block for embedding."""
    lines: list[str] = []
    if headers:
        lines.append(" | ".join(headers))
    for row in rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section tracker
# ---------------------------------------------------------------------------


def _current_section_from_page(page_text: str, fallback: str) -> str:
    """Extract the last ITEM header from the page text, or return fallback."""
    last_title = fallback
    for line in page_text.split("\n"):
        m = _ITEM_RE.match(line.strip())
        if m:
            item_num = m.group(1)
            item_label = m.group(2).strip().rstrip(".")
            item_label = re.sub(r"\s+\d+\s*$", "", item_label)
            last_title = f"Item {item_num}" + (f". {item_label}" if item_label else "")
    return last_title


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_tables(
    pdf_path: Path,
    doc_id: UUID,
    *,
    min_rows: int = 2,
) -> list[TableChunk]:
    """Extract all financial tables from a Tesla SEC filing PDF.

    Args:
        pdf_path: Path to the source PDF file.
        doc_id: Parent :class:`FilingDocument` identifier.
        min_rows: Minimum number of data rows (excluding header) for a
            table to be included.  Very small tables are often layout
            artifacts rather than real financial data.

    Returns:
        List of :class:`TableChunk` instances.
    """
    analysis = analyze_filing_pdf(pdf_path)
    return table_chunks_from_analysis(analysis, doc_id, min_rows=min_rows)


def table_chunks_from_analysis(
    analysis: FilingPdfAnalysis,
    doc_id: UUID,
    *,
    min_rows: int = 2,
) -> list[TableChunk]:
    """Build table chunks from a shared filing analysis result."""
    chunks: list[TableChunk] = []
    current_section = "Unknown"

    for page in analysis.pages:
        page_text = page.text

        # Update section context.
        current_section = _current_section_from_page(page_text, current_section)

        # Build parser provenance from page-level diagnostics.
        provenance = ParserProvenance(
            parser_name=page.parser_used,
            used_fallback=page.used_fallback,
            fallback_reason=page.fallback_reason,
        )

        for table_idx, raw_table in enumerate(page.raw_tables):
            if not raw_table:
                continue

            headers, rows = _clean_table(raw_table)
            if len(rows) < min_rows:
                continue
                
            # Filter out false-positive layout artifacts (1-column tables)
            max_cols = max((len([c for c in r if c]) for r in [headers] + rows), default=0)
            if max_cols < 2:
                continue

            raw_text = _table_to_text(headers, rows)
            if not raw_text.strip():
                continue

            caption = _extract_caption(page_text, table_idx)

            chunk = TableChunk(
                doc_id=doc_id,
                kind=ChunkKind.TABLE,
                page_number=page.page_number,
                section_title=current_section,
                caption=caption,
                headers=headers,
                rows=rows,
                raw_text=raw_text,
                parser_provenance=provenance,
            )

            # Validate numeric cells.
            cell_results = validate_table_cells(chunk)
            if cell_results:
                chunk = chunk.model_copy(
                    update={
                        "cell_validations": cell_results,
                        "validation_status": overall_validation_status(cell_results),
                    }
                )

            chunks.append(chunk)

    return chunks


def _extract_caption(page_text: str, table_idx: int) -> str:
    """Try to extract a table caption/title from surrounding page text.

    Uses a heuristic: look for lines that mention common financial statement
    titles (e.g. "Consolidated Balance Sheets").
    """
    caption_patterns = [
        r"Consolidated\s+Balance\s+Sheets?",
        r"Consolidated\s+Statements?\s+of\s+Operations?",
        r"Consolidated\s+Statements?\s+of\s+Comprehensive\s+(?:Income|Loss)",
        r"Consolidated\s+Statements?\s+of\s+(?:Cash\s+Flows?|Stockholders)",
        r"Consolidated\s+Statements?\s+of\s+(?:Redeemable|Equity)",
        r"Notes?\s+to\s+(?:Consolidated\s+)?Financial\s+Statements?",
        r"Schedule\s+of\s+",
        r"Summary\s+of\s+",
    ]
    matches: list[str] = []
    for line in page_text.split("\n"):
        stripped = line.strip()
        for pat in caption_patterns:
            if re.search(pat, stripped, re.IGNORECASE):
                matches.append(stripped)
                break
    if not matches:
        return ""
    return matches[min(table_idx, len(matches) - 1)]
