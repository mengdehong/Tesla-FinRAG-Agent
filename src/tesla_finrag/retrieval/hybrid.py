"""Hybrid retrieval service combining lexical, vector, and fact search.

Implements :class:`RetrievalService` by fusing results from multiple
search strategies using Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

from uuid import UUID

from tesla_finrag.models import (
    ChunkKind,
    EvidenceBundle,
    FactRecord,
    QueryPlan,
    RetrievalResult,
    SearchMode,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.repositories import CorpusRepository, FactsRepository, RetrievalStore
from tesla_finrag.retrieval.lexical import LexicalSearcher
from tesla_finrag.retrieval.vector import VectorSearcher
from tesla_finrag.services import RetrievalService


def _reciprocal_rank_fusion(
    result_lists: list[list[RetrievalResult]],
    *,
    k: int = 60,
) -> list[RetrievalResult]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    Each result's fused score is ``sum(1 / (k + rank))`` across all lists
    in which it appears, where ``rank`` is 1-indexed.

    Args:
        result_lists: Two or more ranked lists to fuse.
        k: RRF smoothing constant (default 60).

    Returns:
        A single merged list sorted by descending fused score.
    """
    scores: dict[UUID, float] = {}
    best_result: dict[UUID, RetrievalResult] = {}

    for rlist in result_lists:
        for rank, result in enumerate(rlist, start=1):
            rrf_score = 1.0 / (k + rank)
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + rrf_score
            # Keep the result with the higher original score
            if (
                result.chunk_id not in best_result
                or result.score > best_result[result.chunk_id].score
            ):
                best_result[result.chunk_id] = result

    fused: list[RetrievalResult] = []
    for chunk_id, fused_score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        original = best_result[chunk_id]
        fused.append(
            RetrievalResult(
                chunk_id=original.chunk_id,
                doc_id=original.doc_id,
                content=original.content,
                score=fused_score,
                source=SearchMode.HYBRID,
                chunk_type=original.chunk_type,
                metadata=original.metadata,
            )
        )
    return fused


class HybridRetrievalService(RetrievalService):
    """Concrete retrieval service that combines lexical and vector search.

    Uses Reciprocal Rank Fusion to merge results from the lexical searcher
    and vector searcher, then enriches the evidence bundle with matching
    fact records from the facts repository.

    Parameters:
        corpus_repo: Repository for accessing corpus chunks.
        facts_repo: Repository for structured financial facts.
        retrieval_store: Vector index for semantic search.
        embed_fn: Callable that produces embeddings for a query string.
            If ``None``, the vector search lane is skipped.
        lexical_top_k: Number of candidates from lexical search.
        vector_top_k: Number of candidates from vector search.
        final_top_k: Number of chunks in the final fused result.
    """

    def __init__(
        self,
        corpus_repo: CorpusRepository,
        facts_repo: FactsRepository,
        retrieval_store: RetrievalStore | None = None,
        embed_fn: object | None = None,
        *,
        lexical_top_k: int = 15,
        vector_top_k: int = 15,
        final_top_k: int = 8,
    ) -> None:
        self._corpus = corpus_repo
        self._facts = facts_repo
        self._store = retrieval_store
        self._embed_fn = embed_fn
        self._lexical_top_k = lexical_top_k
        self._vector_top_k = vector_top_k
        self._final_top_k = final_top_k

        # Build lexical index from all chunks in the corpus
        self._lexical = LexicalSearcher()
        self._vector = VectorSearcher(retrieval_store) if retrieval_store else None
        self._lexical_indexed = False

    def _ensure_lexical_index(self) -> None:
        """Build lexical index lazily on first retrieval call."""
        if self._lexical_indexed:
            return
        if hasattr(self._corpus, "all_section_chunks"):
            chunks: list[SectionChunk | TableChunk] = list(
                self._corpus.all_section_chunks()  # type: ignore[attr-defined]
            )
            chunks.extend(self._corpus.all_table_chunks())  # type: ignore[attr-defined]
        else:
            # Fallback: iterate known filings
            chunks = []
            for filing in self._corpus.list_filings():
                chunks.extend(self._corpus.get_section_chunks(filing.doc_id))
                chunks.extend(self._corpus.get_table_chunks(filing.doc_id))
        self._lexical.add_chunks(chunks)
        self._lexical_indexed = True

    def _build_query_text(self, plan: QueryPlan) -> str:
        """Build the search query text from the plan."""
        parts = [plan.original_query]
        if plan.retrieval_keywords:
            parts.extend(plan.retrieval_keywords)
        if plan.required_concepts:
            parts.extend(plan.required_concepts)
        return " ".join(parts)

    def _get_doc_ids_for_plan(self, plan: QueryPlan) -> list[UUID] | None:
        """Resolve period filters to doc_ids, or None for no filter."""
        if not plan.required_periods:
            return None
        filings = self._corpus.list_filings()
        matching = [f.doc_id for f in filings if f.period_end in plan.required_periods]
        return matching

    def _get_facts_for_plan(self, plan: QueryPlan) -> list[FactRecord]:
        """Retrieve structured facts matching the query plan."""
        all_facts: list[FactRecord] = []
        if plan.required_concepts:
            for concept in plan.required_concepts:
                for period in plan.required_periods or [None]:  # type: ignore[list-item]
                    all_facts.extend(self._facts.get_facts(concept=concept, period_end=period))
        elif plan.required_periods:
            for period in plan.required_periods:
                all_facts.extend(self._facts.get_facts(period_end=period))
        # Deduplicate by fact_id
        seen: set[UUID] = set()
        unique: list[FactRecord] = []
        for fact in all_facts:
            if fact.fact_id not in seen:
                seen.add(fact.fact_id)
                unique.append(fact)
        return unique

    def _build_chunk_lookup(
        self,
        *,
        doc_ids: list[UUID] | None,
    ) -> tuple[dict[UUID, SectionChunk], dict[UUID, TableChunk]]:
        """Map source chunk ids to canonical processed chunk records."""
        section_by_id: dict[UUID, SectionChunk] = {}
        table_by_id: dict[UUID, TableChunk] = {}
        filings = self._corpus.list_filings()
        doc_id_filter = set(doc_ids) if doc_ids is not None else None
        for filing in filings:
            if doc_id_filter is not None and filing.doc_id not in doc_id_filter:
                continue
            for section in self._corpus.get_section_chunks(filing.doc_id):
                section_by_id[section.chunk_id] = section
            for table in self._corpus.get_table_chunks(filing.doc_id):
                table_by_id[table.chunk_id] = table
        return section_by_id, table_by_id

    def retrieve(self, plan: QueryPlan) -> EvidenceBundle:
        """Retrieve evidence by fusing lexical, vector, and fact results.

        Args:
            plan: Structured query plan from the planning service.

        Returns:
            An :class:`EvidenceBundle` with relevant chunks and facts.
        """
        self._ensure_lexical_index()

        query_text = self._build_query_text(plan)
        doc_ids = self._get_doc_ids_for_plan(plan)

        # --- Lane 1: Lexical search ---
        lexical_results = self._lexical.search(
            query_text, top_k=self._lexical_top_k, doc_ids=doc_ids
        )

        # --- Lane 2: Vector search ---
        vector_results: list[RetrievalResult] = []
        if self._vector and self._embed_fn and callable(self._embed_fn):
            query_embedding = self._embed_fn(query_text)
            if query_embedding:
                vector_results = self._vector.search(
                    query_embedding, top_k=self._vector_top_k, doc_ids=doc_ids
                )

        # --- Lane 3: Structured facts ---
        facts = self._get_facts_for_plan(plan)

        # --- Fusion ---
        lanes = [lane for lane in [lexical_results, vector_results] if lane]
        if lanes:
            fused = _reciprocal_rank_fusion(lanes)[: self._final_top_k]
        else:
            fused = []

        # --- Collect chunks for the bundle ---
        section_lookup, table_lookup = self._build_chunk_lookup(doc_ids=doc_ids)
        section_chunks: list[SectionChunk] = []
        table_chunks: list[TableChunk] = []
        retrieval_scores: dict[str, float] = {}
        seen_sections: set[UUID] = set()
        seen_tables: set[UUID] = set()

        for result in fused:
            retrieval_scores[str(result.chunk_id)] = result.score
            if result.chunk_type == ChunkKind.SECTION:
                section = section_lookup.get(result.chunk_id)
                if section is not None and section.chunk_id not in seen_sections:
                    seen_sections.add(section.chunk_id)
                    section_chunks.append(section)
            elif result.chunk_type == ChunkKind.TABLE:
                table = table_lookup.get(result.chunk_id)
                if table is not None and table.chunk_id not in seen_tables:
                    seen_tables.add(table.chunk_id)
                    table_chunks.append(table)

        return EvidenceBundle(
            plan_id=plan.plan_id,
            section_chunks=section_chunks,
            table_chunks=table_chunks,
            facts=facts,
            retrieval_scores=retrieval_scores,
            metadata={
                "lexical_hits": len(lexical_results),
                "vector_hits": len(vector_results),
                "fact_hits": len(facts),
                "fused_hits": len(fused),
                "query_text": query_text,
                "doc_id_filter": [str(d) for d in doc_ids] if doc_ids is not None else None,
            },
        )
