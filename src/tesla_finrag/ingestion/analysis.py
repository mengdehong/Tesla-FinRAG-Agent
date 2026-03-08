"""Shared filing-level PDF analysis helpers.

Builds a single in-memory view of a filing PDF so narrative chunking and
table normalization can reuse the same page text and extracted tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class FilingPageAnalysis:
    """Cached extraction output for a single PDF page."""

    page_number: int
    text: str
    raw_tables: list[list[list[str | None]]]


@dataclass(frozen=True)
class FilingPdfAnalysis:
    """Shared filing-level extraction result."""

    pdf_path: Path
    pages: tuple[FilingPageAnalysis, ...]


def analyze_filing_pdf(pdf_path: Path) -> FilingPdfAnalysis:
    """Open a filing once and cache page text plus raw table extraction."""
    pages: list[FilingPageAnalysis] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            pages.append(
                FilingPageAnalysis(
                    page_number=page_number,
                    text=page.extract_text() or "",
                    raw_tables=page.extract_tables() or [],
                )
            )
    return FilingPdfAnalysis(pdf_path=pdf_path, pages=tuple(pages))
