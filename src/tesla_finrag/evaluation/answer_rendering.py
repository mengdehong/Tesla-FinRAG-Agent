"""Helpers for rendering answer text in the Streamlit workbench."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

_BLOCK_PATTERN = re.compile(r"(?s)(\$\$(.+?)\$\$|\\\[(.+?)\\\])")


@dataclass(frozen=True)
class AnswerRenderSegment:
    """A renderable segment extracted from answer text."""

    kind: Literal["text", "latex_block"]
    content: str
    original: str | None = None


def split_answer_segments(answer_text: str) -> list[AnswerRenderSegment]:
    """Split answer text into ordered text and block-math segments."""
    segments: list[AnswerRenderSegment] = []
    cursor = 0

    for match in _BLOCK_PATTERN.finditer(answer_text):
        start, end = match.span()
        if start > cursor:
            text = answer_text[cursor:start]
            if text:
                segments.append(AnswerRenderSegment(kind="text", content=text))

        original = match.group(0)
        latex = match.group(2) if match.group(2) is not None else match.group(3)
        segments.append(
            AnswerRenderSegment(
                kind="latex_block",
                content=latex.strip(),
                original=original,
            )
        )
        cursor = end

    if cursor < len(answer_text):
        tail = answer_text[cursor:]
        if tail:
            segments.append(AnswerRenderSegment(kind="text", content=tail))

    if not segments:
        return [AnswerRenderSegment(kind="text", content=answer_text)]
    return segments


def render_answer_segments(
    answer_text: str,
    *,
    markdown_renderer: Callable[[str], None],
    latex_renderer: Callable[[str], None],
    plain_text_renderer: Callable[[str], None],
) -> None:
    """Render answer text with block LaTeX support and fail-open fallback."""
    for segment in split_answer_segments(answer_text):
        if segment.kind == "text":
            markdown_renderer(segment.content)
            continue

        try:
            latex_renderer(segment.content)
        except Exception:
            plain_text_renderer(segment.original or segment.content)
