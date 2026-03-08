"""Helpers for rendering answer text with optional block LaTeX support."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

_BLOCK_MATH_PATTERN = re.compile(
    r"(?s)(\$\$(?P<dollar>.+?)\$\$|\\\[(?P<bracket>.+?)\\\])"
)


@dataclass(frozen=True)
class AnswerSegment:
    """A renderable answer segment."""

    kind: Literal["text", "latex_block"]
    content: str
    raw: str | None = None


def split_answer_segments(answer_text: str) -> list[AnswerSegment]:
    """Split answer text into ordered plain-text and block-math segments."""
    segments: list[AnswerSegment] = []
    cursor = 0

    for match in _BLOCK_MATH_PATTERN.finditer(answer_text):
        start, end = match.span()
        if start > cursor:
            segments.append(AnswerSegment(kind="text", content=answer_text[cursor:start]))

        latex_content = match.group("dollar") or match.group("bracket") or ""
        segments.append(
            AnswerSegment(
                kind="latex_block",
                content=latex_content.strip(),
                raw=match.group(0),
            )
        )
        cursor = end

    if cursor < len(answer_text):
        segments.append(AnswerSegment(kind="text", content=answer_text[cursor:]))

    if not segments:
        return [AnswerSegment(kind="text", content=answer_text)]
    return segments


def render_answer_segments(
    answer_text: str,
    *,
    markdown_renderer: Callable[[str], None],
    latex_renderer: Callable[[str], None],
    plain_text_renderer: Callable[[str], None],
) -> None:
    """Render text and block LaTeX segments with fail-open fallback."""
    for segment in split_answer_segments(answer_text):
        if segment.kind == "text":
            if segment.content:
                markdown_renderer(segment.content)
            continue

        try:
            latex_renderer(segment.content)
        except Exception:
            plain_text_renderer(segment.raw or segment.content)
