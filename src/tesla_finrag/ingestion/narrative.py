"""Section-aware narrative parsing from Tesla SEC filing PDFs.

Splits each filing into section-delimited narrative chunks with full
provenance metadata (page number, section path, document identity).
Tables encountered during parsing are *skipped* here — they are handled
by the dedicated table extractor in :mod:`tesla_finrag.ingestion.tables`.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from tesla_finrag.models import ChunkKind, SectionChunk

# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

# Matches "ITEM 1.", "Item 7A.", "ITEM 1A" (with optional trailing dot/colon).
_ITEM_RE = re.compile(
    r"^(?:ITEM|Item)\s+(\d+[A-Z]?)[\.\:\s]?\s*(.*)",
    re.IGNORECASE,
)

# Matches "PART I.", "PART II" etc.
_PART_RE = re.compile(
    r"^(?:PART|Part)\s+([IV]+)[\.\:\s]?\s*(.*)",
    re.IGNORECASE,
)

# If a page has this many or more ITEM matches it's likely a TOC page.
_TOC_ITEM_THRESHOLD = 5

# Approximate characters per token for rough counting.
_CHARS_PER_TOKEN = 4

# Target maximum tokens per chunk.
_MAX_CHUNK_TOKENS = 800

# Overlap tokens between consecutive chunks in the same section.
_OVERLAP_TOKENS = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_toc_page(text: str) -> bool:
    """Heuristic: a page with many ITEM headers is likely a table of contents."""
    count = sum(1 for line in text.split("\n") if _ITEM_RE.match(line.strip()))
    return count >= _TOC_ITEM_THRESHOLD


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _detect_sections(pages: list[tuple[int, str]]) -> list[tuple[str, int, str]]:
    """Walk pages and detect section boundaries.

    Returns a list of ``(section_title, start_page, concatenated_text)``
    tuples.  Pages before the first detected section are grouped under a
    synthetic "Preamble" section.
    """
    sections: list[tuple[str, int, list[str]]] = []
    current_title = "Preamble"
    current_start_page = 1
    current_texts: list[str] = []

    for page_num, text in pages:
        if _is_toc_page(text):
            # Skip TOC pages for section detection — the same headers
            # appear again when actual content begins.
            continue

        lines = text.split("\n")
        page_buffer: list[str] = []
        for line in lines:
            stripped = line.strip()
            m = _ITEM_RE.match(stripped)
            if m:
                buffered_text = "\n".join(page_buffer).strip()
                if buffered_text:
                    current_texts.append(buffered_text)
                if current_texts:
                    sections.append((current_title, current_start_page, current_texts))
                item_num = m.group(1)
                item_label = m.group(2).strip().rstrip(".")
                item_label = re.sub(r"\s+\d+\s*$", "", item_label)
                current_title = f"Item {item_num}" + (f". {item_label}" if item_label else "")
                current_start_page = page_num
                current_texts = []
                page_buffer = [line]
                continue
            page_buffer.append(line)

        buffered_text = "\n".join(page_buffer).strip()
        if buffered_text:
            current_texts.append(buffered_text)

    # Flush last section.
    if current_texts:
        sections.append((current_title, current_start_page, current_texts))

    # Merge page texts into single strings.
    return [(title, start_page, "\n\n".join(texts)) for title, start_page, texts in sections]


def _chunk_text(
    text: str,
    max_tokens: int = _MAX_CHUNK_TOKENS,
    overlap_tokens: int = _OVERLAP_TOKENS,
) -> list[tuple[str, int]]:
    """Split *text* into overlapping chunks.

    Returns ``(chunk_text, char_offset)`` pairs.
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    if _estimate_tokens(text) <= max_tokens:
        return [(text, 0)]

    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = start + max_chars

        # Try to break at a paragraph or sentence boundary.
        if end < len(text):
            # Prefer paragraph break.
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + max_chars // 2:
                end = para_break
            else:
                # Sentence break.
                sent_break = text.rfind(". ", start, end)
                if sent_break > start + max_chars // 2:
                    end = sent_break + 1  # include the period

        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start))

        # Advance with overlap.
        start = end - overlap_chars
        if start <= (chunks[-1][1] if chunks else 0):
            start = end  # Prevent infinite loop on very short text.

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_narrative(
    pdf_path: Path,
    doc_id: UUID,
    *,
    max_chunk_tokens: int = _MAX_CHUNK_TOKENS,
    overlap_tokens: int = _OVERLAP_TOKENS,
) -> list[SectionChunk]:
    """Extract section-aware narrative chunks from a Tesla SEC filing PDF.

    Args:
        pdf_path: Path to the source PDF file.
        doc_id: Parent :class:`FilingDocument` identifier.
        max_chunk_tokens: Approximate maximum tokens per chunk.
        overlap_tokens: Overlap between consecutive chunks in the same section.

    Returns:
        List of :class:`SectionChunk` instances with provenance metadata.
    """
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((i, text))

    sections = _detect_sections(pages)
    chunks: list[SectionChunk] = []

    for section_title, start_page, section_text in sections:
        if not section_text.strip():
            continue
        text_chunks = _chunk_text(section_text, max_chunk_tokens, overlap_tokens)
        for chunk_text, char_offset in text_chunks:
            if not chunk_text.strip():
                continue
            chunks.append(
                SectionChunk(
                    doc_id=doc_id,
                    kind=ChunkKind.SECTION,
                    page_number=start_page,
                    char_offset=char_offset,
                    section_title=section_title,
                    text=chunk_text,
                    token_count=_estimate_tokens(chunk_text),
                )
            )

    return chunks
