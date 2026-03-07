"""Evidence linking service.

Aligns narrative chunks, table chunks, and fact records around shared
periods and metrics so the answer composer receives a coherent evidence
set rather than a flat list of unrelated hits.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from tesla_finrag.models import (
    EvidenceBundle,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.repositories import CorpusRepository, FactsRepository


class EvidenceLinker:
    """Link evidence items around shared periods and metrics.

    Given an :class:`EvidenceBundle` from hybrid retrieval, the linker
    enriches it by:

    1. Grouping chunks by their parent filing's ``period_end`` date.
    2. For each period, pulling in fact records that share the same
       period and any required concepts.
    3. For each period, pulling in table chunks from the same filing
       that mention matching metrics.

    This ensures the answer composer sees *all* evidence for a given
    period, not just the top-k retrieval hits.

    Parameters:
        corpus_repo: Repository for filing metadata and corpus chunks.
        facts_repo: Repository for structured financial facts.
    """

    def __init__(
        self,
        corpus_repo: CorpusRepository,
        facts_repo: FactsRepository,
    ) -> None:
        self._corpus = corpus_repo
        self._facts = facts_repo

    def _filing_period_end(self, doc_id: UUID) -> date | None:
        """Look up the period_end date for a filing."""
        filing = self._corpus.get_filing(doc_id)
        return filing.period_end if filing else None

    def _group_by_period(
        self,
        section_chunks: list[SectionChunk],
        table_chunks: list[TableChunk],
    ) -> dict[date, tuple[list[SectionChunk], list[TableChunk]]]:
        """Group chunks by their parent filing's period_end date."""
        groups: dict[date, tuple[list[SectionChunk], list[TableChunk]]] = defaultdict(
            lambda: ([], [])
        )
        for chunk in section_chunks:
            period = self._filing_period_end(chunk.doc_id)
            if period:
                groups[period][0].append(chunk)
        for chunk in table_chunks:
            period = self._filing_period_end(chunk.doc_id)
            if period:
                groups[period][1].append(chunk)
        return dict(groups)

    def link(
        self,
        bundle: EvidenceBundle,
        *,
        required_concepts: list[str] | None = None,
        required_periods: list[date] | None = None,
    ) -> EvidenceBundle:
        """Enrich an evidence bundle by linking across periods and metrics.

        Args:
            bundle: The raw evidence bundle from hybrid retrieval.
            required_concepts: XBRL concepts to look up in the facts store.
            required_periods: If given, restrict linking to these periods.

        Returns:
            A new :class:`EvidenceBundle` with additional linked evidence.
        """
        section_chunks = list(bundle.section_chunks)
        table_chunks = list(bundle.table_chunks)
        facts = list(bundle.facts)
        scores = dict(bundle.retrieval_scores)

        # Determine which periods are represented
        period_groups = self._group_by_period(section_chunks, table_chunks)
        relevant_periods = set(period_groups.keys())

        # If specific periods are required, add them even if no chunks matched
        if required_periods:
            relevant_periods.update(required_periods)

        # For each period, enrich with related facts and table chunks
        existing_chunk_ids = {c.chunk_id for c in section_chunks} | {
            c.chunk_id for c in table_chunks
        }
        existing_fact_ids = {f.fact_id for f in facts}

        for period in relevant_periods:
            # Pull in facts for this period
            if required_concepts:
                for concept in required_concepts:
                    period_facts = self._facts.get_facts(concept=concept, period_end=period)
                    for fact in period_facts:
                        if fact.fact_id not in existing_fact_ids:
                            facts.append(fact)
                            existing_fact_ids.add(fact.fact_id)
            else:
                # Get all facts for this period
                period_facts = self._facts.get_facts(period_end=period)
                for fact in period_facts:
                    if fact.fact_id not in existing_fact_ids:
                        facts.append(fact)
                        existing_fact_ids.add(fact.fact_id)

            # Pull in table chunks from filings matching this period
            for filing in self._corpus.list_filings():
                if filing.period_end != period:
                    continue
                for tbl in self._corpus.get_table_chunks(filing.doc_id):
                    if tbl.chunk_id not in existing_chunk_ids:
                        # Only add tables that mention a required concept
                        if required_concepts and not self._table_mentions_concept(
                            tbl, required_concepts
                        ):
                            continue
                        table_chunks.append(tbl)
                        existing_chunk_ids.add(tbl.chunk_id)
                        scores[str(tbl.chunk_id)] = 0.0  # linked, not retrieved

        return EvidenceBundle(
            plan_id=bundle.plan_id,
            section_chunks=section_chunks,
            table_chunks=table_chunks,
            facts=facts,
            retrieval_scores=scores,
            metadata={
                **bundle.metadata,
                "linked_periods": sorted(str(p) for p in relevant_periods),
                "linked_facts_count": len(facts) - len(bundle.facts),
                "linked_tables_count": len(table_chunks) - len(bundle.table_chunks),
            },
        )

    @staticmethod
    def _table_mentions_concept(table: TableChunk, concepts: list[str]) -> bool:
        """Check if a table chunk's text mentions any of the concepts."""
        searchable = f"{table.caption} {table.raw_text} {' '.join(table.headers)}".lower()
        for concept in concepts:
            # Extract the human-readable part of the concept name
            label = concept.split(":")[-1] if ":" in concept else concept
            # Convert camelCase to space-separated words for matching
            words = []
            current: list[str] = []
            for ch in label:
                if ch.isupper() and current:
                    words.append("".join(current).lower())
                    current = [ch]
                else:
                    current.append(ch)
            if current:
                words.append("".join(current).lower())
            label_lower = " ".join(words)
            if label_lower in searchable:
                return True
            # Try without trailing 's' (e.g. "revenues" -> "revenue")
            if label_lower.endswith("s") and label_lower[:-1] in searchable:
                return True
            # Try adding trailing 's' (e.g. "revenue" -> "revenues")
            if f"{label_lower}s" in searchable:
                return True
        return False
