"""Shared filing-level PDF analysis helpers.

Builds a single in-memory view of a filing PDF so narrative chunking and
table normalization can reuse the same page text and extracted tables.

Supports an optional local fallback parser (PyMuPDF / ``fitz``) when the
primary ``pdfplumber`` extraction returns empty or incomplete output for
a given page.  Fallback usage is tracked per-page so downstream code and
operators can see exactly where the primary parser failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional fallback parser
# ---------------------------------------------------------------------------

try:
    import fitz as _fitz  # PyMuPDF

    _HAS_PYMUPDF = True
except ImportError:  # pragma: no cover
    _fitz = None  # type: ignore[assignment]
    _HAS_PYMUPDF = False


# ---------------------------------------------------------------------------
# Diagnostic dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageParserDiagnostic:
    """Diagnostic record for a single page's extraction outcome."""

    page_number: int
    parser_used: str  # "pdfplumber" or "pymupdf"
    used_fallback: bool
    fallback_reason: str | None = None
    text_chars: int = 0
    table_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FilingPageAnalysis:
    """Cached extraction output for a single PDF page."""

    page_number: int
    text: str
    raw_tables: list[list[list[str | None]]]
    parser_used: str = "pdfplumber"
    used_fallback: bool = False
    fallback_reason: str | None = None


@dataclass(frozen=True)
class FilingPdfAnalysis:
    """Shared filing-level extraction result."""

    pdf_path: Path
    pages: tuple[FilingPageAnalysis, ...]
    diagnostics: tuple[PageParserDiagnostic, ...] = ()

    @property
    def fallback_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.used_fallback)

    @property
    def failed_pages(self) -> list[PageParserDiagnostic]:
        return [d for d in self.diagnostics if d.error is not None]


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------


def _pymupdf_extract_page(
    pdf_path: Path, page_index: int
) -> tuple[str, list[list[list[str | None]]]]:
    """Extract text and tables from a single page using PyMuPDF."""
    if _fitz is None:
        return "", []

    doc = _fitz.open(str(pdf_path))  # type: ignore[union-attr]
    try:
        page = doc[page_index]
        text = page.get_text() or ""
        # PyMuPDF doesn't have native table extraction as rich as pdfplumber,
        # so we return empty tables and rely on text-only fallback.
        return text, []
    finally:
        doc.close()


def _page_needs_fallback(text: str, raw_tables: list[list[list[str | None]]]) -> str | None:
    """Return a fallback reason if primary extraction looks unusable, else ``None``."""
    text_stripped = text.strip()
    if not text_stripped:
        return "empty_text"
    # A page with very little text (<20 chars) and no tables is suspicious.
    if len(text_stripped) < 20 and not raw_tables:
        return "insufficient_text"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_filing_pdf(
    pdf_path: Path,
    *,
    enable_fallback: bool = True,
) -> FilingPdfAnalysis:
    """Open a filing once and cache page text plus raw table extraction.

    When *enable_fallback* is True and PyMuPDF is installed, pages where the
    primary ``pdfplumber`` extraction returns empty or very short text will
    be re-extracted with PyMuPDF as a best-effort fallback.  Per-page
    diagnostics are recorded regardless of fallback availability.
    """
    pages: list[FilingPageAnalysis] = []
    diagnostics: list[PageParserDiagnostic] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_number = page_index + 1
            text = page.extract_text() or ""
            raw_tables = page.extract_tables() or []

            fallback_reason = _page_needs_fallback(text, raw_tables)
            used_fallback = False
            parser_used = "pdfplumber"
            error: str | None = None

            if fallback_reason and enable_fallback and _HAS_PYMUPDF:
                try:
                    fb_text, fb_tables = _pymupdf_extract_page(pdf_path, page_index)
                    if fb_text.strip():
                        text = fb_text
                        raw_tables = fb_tables if fb_tables else raw_tables
                        used_fallback = True
                        parser_used = "pymupdf"
                        logger.info(
                            "Page %d of %s: fallback to PyMuPDF (%s)",
                            page_number,
                            pdf_path.name,
                            fallback_reason,
                        )
                    else:
                        error = f"fallback_also_empty: {fallback_reason}"
                except Exception as exc:
                    error = f"fallback_error: {exc}"
                    logger.warning(
                        "Page %d of %s: PyMuPDF fallback failed: %s",
                        page_number,
                        pdf_path.name,
                        exc,
                    )
            elif fallback_reason and enable_fallback and not _HAS_PYMUPDF:
                error = f"no_fallback_available: {fallback_reason}"

            pages.append(
                FilingPageAnalysis(
                    page_number=page_number,
                    text=text,
                    raw_tables=raw_tables,
                    parser_used=parser_used,
                    used_fallback=used_fallback,
                    fallback_reason=fallback_reason,
                )
            )
            diagnostics.append(
                PageParserDiagnostic(
                    page_number=page_number,
                    parser_used=parser_used,
                    used_fallback=used_fallback,
                    fallback_reason=fallback_reason,
                    text_chars=len(text),
                    table_count=len(raw_tables),
                    error=error,
                )
            )

    return FilingPdfAnalysis(
        pdf_path=pdf_path,
        pages=tuple(pages),
        diagnostics=tuple(diagnostics),
    )
