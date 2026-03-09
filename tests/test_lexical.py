from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from tesla_finrag.models import SearchMode, SectionChunk, TableChunk
from tesla_finrag.retrieval.lexical import LexicalSearcher


def test_lexical_search_returns_bm25_ranked_results() -> None:
    doc_id = uuid4()
    section = SectionChunk(
        doc_id=doc_id,
        section_title="Revenue growth",
        text="Tesla revenue increased significantly in 2023.",
        token_count=7,
    )
    table = TableChunk(
        doc_id=doc_id,
        section_title="Cost breakdown",
        caption="Operating expenses",
        headers=["Metric", "Value"],
        rows=[["R&D", "100"]],
        raw_text="Metric | Value\nR&D | 100",
    )

    searcher = LexicalSearcher()
    searcher.add_chunks([section, table])

    results = searcher.search("revenue", top_k=2)

    assert len(results) == 1
    assert results[0].chunk_id == section.chunk_id
    assert results[0].source == SearchMode.LEXICAL


def test_lexical_search_does_not_rebuild_term_frequency_per_query() -> None:
    doc_id = uuid4()
    section = SectionChunk(
        doc_id=doc_id,
        section_title="Revenue",
        text="Revenue revenue margin",
        token_count=3,
    )
    searcher = LexicalSearcher()
    searcher.add_chunks([section])

    with patch(
        "tesla_finrag.retrieval.lexical.Counter",
        side_effect=AssertionError("Counter should not be constructed in search()"),
    ):
        results = searcher.search("revenue")

    assert len(results) == 1
    assert results[0].chunk_id == section.chunk_id
