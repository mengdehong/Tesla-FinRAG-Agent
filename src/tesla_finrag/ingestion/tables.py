"""Table extraction and normalization from Tesla SEC filing PDFs.

Each financial table is emitted as an independent :class:`TableChunk` with
structured metadata (headers, rows) and a serialised fallback ``raw_text``
suitable for embedding.  Tables are not merged into surrounding narrative
text.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from tesla_finrag.models import ChunkKind, TableChunk

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
        lines.append(" | ".join(h for h in headers if h))
    for row in rows:
        lines.append(" | ".join(c for c in row if c))
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
    chunks: list[TableChunk] = []
    current_section = "Unknown"

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_num = page_idx + 1
            page_text = page.extract_text() or ""

            # Update section context.
            current_section = _current_section_from_page(page_text, current_section)

            # Extract tables from this page.
            tables = page.extract_tables() or []
            for table_idx, raw_table in enumerate(tables):
                if not raw_table:
                    continue

                headers, rows = _clean_table(raw_table)
                if len(rows) < min_rows:
                    continue

                raw_text = _table_to_text(headers, rows)
                if not raw_text.strip():
                    continue

                # Try to extract a caption from nearby text.
                caption = _extract_caption(page_text, table_idx)

                chunks.append(
                    TableChunk(
                        doc_id=doc_id,
                        kind=ChunkKind.TABLE,
                        page_number=page_num,
                        section_title=current_section,
                        caption=caption,
                        headers=headers,
                        rows=rows,
                        raw_text=raw_text,
                    )
                )

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
    for line in page_text.split("\n"):
        stripped = line.strip()
        for pat in caption_patterns:
            if re.search(pat, stripped, re.IGNORECASE):
                return stripped
    return ""
