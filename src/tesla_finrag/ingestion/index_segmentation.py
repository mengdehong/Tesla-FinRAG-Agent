"""Index-time chunk segmentation helpers for embedding-safe LanceDB rows."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tesla_finrag.models import SectionChunk, TableChunk

_DEFAULT_MAX_CHARS = 2400
_DEFAULT_OVERLAP_CHARS = 180
_DEFAULT_TABLE_HEADER_LINES = 2


@dataclass(frozen=True)
class ChunkSegment:
    """Single embedding-safe segment derived from a processed chunk."""

    text: str
    segment_index: int
    segment_count: int


def segment_chunk_for_indexing(
    chunk: SectionChunk | TableChunk,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    table_header_lines: int = _DEFAULT_TABLE_HEADER_LINES,
) -> list[ChunkSegment]:
    """Split one processed chunk into embedding-safe segments."""
    budget = max(256, max_chars)
    overlap = max(0, min(overlap_chars, budget - 1))

    if isinstance(chunk, SectionChunk):
        raw_segments = _segment_narrative_text(chunk.text, max_chars=budget)
        finalize_overlap = overlap
    else:
        raw_segments = _segment_table_text(
            chunk.raw_text,
            max_chars=budget,
            table_header_lines=max(1, table_header_lines),
        )
        # Preserve repeated table header context exactly; do not prepend generic overlap tails.
        finalize_overlap = 0

    finalized = _finalize_segments(
        raw_segments,
        max_chars=budget,
        overlap_chars=finalize_overlap,
    )
    return [
        ChunkSegment(text=text, segment_index=index, segment_count=len(finalized))
        for index, text in enumerate(finalized)
    ]


def _segment_narrative_text(text: str, *, max_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return [""]
    if len(cleaned) <= max_chars:
        return [cleaned]

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    if not paragraphs:
        return _hard_split(cleaned, max_chars=max_chars, overlap_chars=0)

    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentence_parts = _split_sentences(paragraph)
        if len(sentence_parts) <= 1:
            units.extend(_hard_split(paragraph, max_chars=max_chars, overlap_chars=0))
            continue
        for sentence in sentence_parts:
            if len(sentence) <= max_chars:
                units.append(sentence)
            else:
                units.extend(_hard_split(sentence, max_chars=max_chars, overlap_chars=0))

    segments: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}\n\n{unit}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            segments.append(current.strip())
        current = unit
    if current:
        segments.append(current.strip())
    return segments


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _segment_table_text(
    text: str,
    *,
    max_chars: int,
    table_header_lines: int,
) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return [""]
    if len(cleaned) <= max_chars:
        return [cleaned]

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) <= 1:
        return _hard_split(cleaned, max_chars=max_chars, overlap_chars=0)

    header_lines = lines[:table_header_lines]
    body_lines = lines[table_header_lines:] or lines[1:]
    header = "\n".join(header_lines).strip()
    can_repeat_header = bool(header) and len(header) <= max_chars // 3
    repeated_header = header if can_repeat_header else ""
    available_body = max(64, max_chars - len(repeated_header) - (1 if repeated_header else 0))
    source_lines = body_lines if can_repeat_header else [*header_lines, *body_lines]

    body_segments: list[str] = []
    current_lines: list[str] = []
    current_size = 0
    for line in source_lines:
        for line_text in _split_table_line(line, max_chars=available_body):
            projected = current_size + len(line_text) + (1 if current_lines else 0)
            if projected <= available_body:
                current_lines.append(line_text)
                current_size = projected
                continue
            if current_lines:
                body_segments.append("\n".join(current_lines))
            current_lines = [line_text]
            current_size = len(line_text)
    if current_lines:
        body_segments.append("\n".join(current_lines))

    if not body_segments:
        return _hard_split(cleaned, max_chars=max_chars, overlap_chars=0)

    segments = [
        f"{repeated_header}\n{body}".strip() if repeated_header else body.strip()
        for body in body_segments
        if body
    ]
    return segments or _hard_split(cleaned, max_chars=max_chars, overlap_chars=0)


def _split_table_line(line: str, *, max_chars: int) -> list[str]:
    cleaned = line.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    return _hard_split(cleaned, max_chars=max_chars, overlap_chars=0)


def _finalize_segments(
    raw_segments: list[str],
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    expanded: list[str] = []
    for item in raw_segments:
        cleaned = item.strip()
        if not cleaned:
            continue
        if len(cleaned) <= max_chars:
            expanded.append(cleaned)
            continue
        expanded.extend(_hard_split(cleaned, max_chars=max_chars, overlap_chars=overlap_chars))

    if not expanded:
        expanded = [""]

    if overlap_chars <= 0 or len(expanded) < 2:
        return expanded

    with_overlap: list[str] = [expanded[0]]
    for segment in expanded[1:]:
        previous_tail = with_overlap[-1][-overlap_chars:].strip()
        if previous_tail:
            combined = f"{previous_tail}\n{segment}".strip()
            if len(combined) <= max_chars:
                with_overlap.append(combined)
                continue
        with_overlap.append(segment)
    return with_overlap


def _hard_split(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return [cleaned]

    overlap = max(0, min(overlap_chars, max_chars - 1))
    segments: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        piece = cleaned[start:end].strip()
        if piece:
            segments.append(piece)
        if end >= len(cleaned):
            break
        start = end - overlap if overlap > 0 else end
    return segments
