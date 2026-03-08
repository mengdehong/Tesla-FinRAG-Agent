"""Regression tests for workbench answer rendering helpers."""

from __future__ import annotations

from tesla_finrag.evaluation.answer_rendering import render_answer_segments, split_answer_segments


def test_split_answer_segments_keeps_mixed_content_order() -> None:
    answer_text = (
        "Revenue summary:\n"
        "$$x = \\frac{a}{b}$$\n"
        "Narrative bridge.\n"
        "\\[y = m \\times n\\]\n"
        "Closing notes."
    )

    segments = split_answer_segments(answer_text)

    assert [segment.kind for segment in segments] == [
        "text",
        "latex_block",
        "text",
        "latex_block",
        "text",
    ]
    assert segments[1].content == r"x = \frac{a}{b}"
    assert segments[3].content == r"y = m \times n"


def test_split_answer_segments_keeps_currency_in_plain_text_lane() -> None:
    answer_text = "Tesla revenue in 2023 was $96.77B and gross margin was 18.2%."

    segments = split_answer_segments(answer_text)

    assert len(segments) == 1
    assert segments[0].kind == "text"
    assert segments[0].content == answer_text


def test_render_answer_segments_fallback_does_not_block_followup_views() -> None:
    answer_text = "Answer intro. $$\\bad{x$$ Answer tail."
    calls: list[tuple[str, str]] = []

    def render_markdown(text: str) -> None:
        calls.append(("markdown", text))

    def render_latex(text: str) -> None:
        if "\\bad{" in text:
            raise ValueError("invalid latex block")
        calls.append(("latex", text))

    def render_plain(text: str) -> None:
        calls.append(("plain", text))

    render_answer_segments(
        answer_text,
        markdown_renderer=render_markdown,
        latex_renderer=render_latex,
        plain_text_renderer=render_plain,
    )

    assert calls == [
        ("markdown", "Answer intro. "),
        ("plain", "$$\\bad{x$$"),
        ("markdown", " Answer tail."),
    ]

    visible_sections: list[str] = []
    visible_sections.append("citations")
    visible_sections.append("retrieval_debug")
    assert visible_sections == ["citations", "retrieval_debug"]
