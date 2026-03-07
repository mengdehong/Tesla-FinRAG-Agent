"""Cosine-similarity vector search over pre-embedded corpus chunks.

Wraps the :class:`RetrievalStore` interface and converts raw search
results into :class:`RetrievalResult` objects.
"""

from __future__ import annotations

from uuid import UUID

from tesla_finrag.models import ChunkKind, RetrievalResult, SearchMode, SectionChunk
from tesla_finrag.repositories import RetrievalStore


class VectorSearcher:
    """Thin wrapper that converts :class:`RetrievalStore` search output to
    :class:`RetrievalResult` instances.

    Parameters:
        store: The vector index backend.
    """

    def __init__(self, store: RetrievalStore) -> None:
        self._store = store

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[RetrievalResult]:
        """Return the top-k vector-similar chunks.

        Args:
            query_embedding: Dense embedding for the query.
            top_k: Maximum results.
            doc_ids: Restrict search to these filings.

        Returns:
            Ranked list of :class:`RetrievalResult`.
        """
        hits = self._store.search(query_embedding, top_k=top_k, doc_ids=doc_ids)

        results: list[RetrievalResult] = []
        for chunk, score in hits:
            if isinstance(chunk, SectionChunk):
                content = chunk.text
                chunk_type = ChunkKind.SECTION
            else:
                content = chunk.raw_text
                chunk_type = ChunkKind.TABLE
            results.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    content=content,
                    score=score,
                    source=SearchMode.VECTOR,
                    chunk_type=chunk_type,
                )
            )
        return results
