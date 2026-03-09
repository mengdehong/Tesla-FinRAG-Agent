from tesla_finrag.evaluation.answer_rendering import render_answer_segments, split_answer_segments


def test_split_answer_segments_extracts_block_math_in_order() -> None:
    segments = split_answer_segments("Revenue was $96.77B.\n\n$$x = y / z$$\n\nDone.")

    assert [segment.kind for segment in segments] == ["text", "latex_block", "text"]
    assert segments[0].content == "Revenue was $96.77B.\n\n"
    assert segments[1].content == "x = y / z"
    assert segments[2].content == "\n\nDone."


def test_split_answer_segments_keeps_currency_text_as_plain_text() -> None:
    segments = split_answer_segments("Tesla revenue was $96.77B in FY2023.")

    assert len(segments) == 1
    assert segments[0].kind == "text"
    assert segments[0].content == "Tesla revenue was $96.77B in FY2023."


def test_render_answer_segments_falls_back_to_plain_text_on_latex_error() -> None:
    calls: list[tuple[str, str]] = []

    def markdown_renderer(text: str) -> None:
        calls.append(("markdown", text))

    def latex_renderer(text: str) -> None:
        calls.append(("latex", text))
        raise ValueError("bad latex")

    def plain_text_renderer(text: str) -> None:
        calls.append(("plain", text))

    render_answer_segments(
        "Before\n\n\\[x^2\\]\n\nAfter",
        markdown_renderer=markdown_renderer,
        latex_renderer=latex_renderer,
        plain_text_renderer=plain_text_renderer,
    )

    assert calls == [
        ("markdown", "Before\n\n"),
        ("latex", "x^2"),
        ("plain", "\\[x^2\\]"),
        ("markdown", "\n\nAfter"),
    ]
