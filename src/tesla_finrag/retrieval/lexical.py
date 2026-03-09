"""BM25-style lexical search over corpus chunks.

Uses term-frequency / inverse-document-frequency scoring without
external dependencies.  Supports metadata pre-filtering by doc_id.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from uuid import UUID

from tesla_finrag.models import ChunkKind, RetrievalResult, SearchMode, SectionChunk, TableChunk


def _tokenize(text: str) -> list[str]:
    """Lowercase and split on non-alphanumeric boundaries."""
    return re.findall(r"[a-z0-9]+", text.lower())


class LexicalSearcher:
    """BM25-style lexical search over section and table chunks.

    Call :meth:`add_chunks` to build the index, then :meth:`search` to
    retrieve matching chunks.

    Parameters:
        k1: BM25 term-frequency saturation parameter.
        b: BM25 document-length normalisation parameter.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._docs: list[tuple[SectionChunk | TableChunk, Counter[str], int]] = []
        self._df: Counter[str] = Counter()
        self._total_tokens: int = 0
        self._avg_dl: float = 0.0

    # -- Indexing --------------------------------------------------------------

    def _text_for_chunk(self, chunk: SectionChunk | TableChunk) -> str:
        if isinstance(chunk, SectionChunk):
            return f"{chunk.section_title} {chunk.text}"
        return f"{chunk.section_title} {chunk.caption} {chunk.raw_text}"

    def add_chunks(self, chunks: list[SectionChunk | TableChunk]) -> None:
        """Index a batch of chunks for lexical search."""
        for chunk in chunks:
            tokens = _tokenize(self._text_for_chunk(chunk))
            tf_map = Counter(tokens)
            dl = len(tokens)
            self._docs.append((chunk, tf_map, dl))
            self._df.update(set(tokens))
            self._total_tokens += dl

        n_docs = len(self._docs)
        self._avg_dl = self._total_tokens / n_docs if n_docs > 0 else 1.0

    # -- Search ----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[RetrievalResult]:
        """Return the top-k chunks matching ``query`` by BM25 score.

        Args:
            query: The search query string.
            top_k: Maximum number of results.
            doc_ids: If given, restrict results to these filing documents.

        Returns:
            Ranked list of :class:`RetrievalResult`.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        n = len(self._docs)
        if n == 0:
            return []

        id_set = set(doc_ids) if doc_ids is not None else None

        scored: list[tuple[SectionChunk | TableChunk, float]] = []
        for chunk, tf_map, dl in self._docs:
            if id_set is not None and chunk.doc_id not in id_set:
                continue

            score = 0.0
            for qt in query_tokens:
                tf = tf_map.get(qt, 0)
                if tf == 0:
                    continue
                df = self._df.get(qt, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                tf_norm = (tf * (self._k1 + 1)) / (
                    tf + self._k1 * (1 - self._b + self._b * dl / self._avg_dl)
                )
                score += idf * tf_norm
            if score > 0:
                scored.append((chunk, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[RetrievalResult] = []
        for chunk, score in scored[:top_k]:
            content = chunk.text if isinstance(chunk, SectionChunk) else chunk.raw_text
            chunk_type = ChunkKind.SECTION if isinstance(chunk, SectionChunk) else ChunkKind.TABLE
            results.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    content=content,
                    score=score,
                    source=SearchMode.LEXICAL,
                    chunk_type=chunk_type,
                )
            )
        return results
