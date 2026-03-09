"""Evidence linking service.

Aligns narrative chunks, table chunks, and fact records around shared
periods and metrics so the answer composer receives a coherent evidence
set rather than a flat list of unrelated hits.

When a plan contains period-aware sub-queries, linking validates
that each required period has adequate coverage and annotates the
metadata with per-period evidence counts.

Phase C adds *table fallback*: when a required concept is not in the
XBRL fact store, the linker scans table chunks for matching numeric
values and creates table-backed :class:`FactRecord` instances.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from uuid import UUID, uuid4

from tesla_finrag.models import (
    EvidenceBundle,
    FactRecord,
    PeriodSemantics,
    SectionChunk,
    SemanticScope,
    TableChunk,
)
from tesla_finrag.repositories import CorpusRepository, FactsRepository

# ---------------------------------------------------------------------------
# Human-readable aliases for table fallback matching
# ---------------------------------------------------------------------------

# Maps XBRL concepts to human-readable search terms used when scanning
# table chunk text for matching line items.  Only concepts that are commonly
# missing from the XBRL fact store need entries here.
_CONCEPT_TABLE_ALIASES: dict[str, list[str]] = {
    "us-gaap:CostOfGoodsAndServicesSold": [
        "cost of automotive revenue",
        "cost of revenue",
        "cost of goods sold",
        "cost of automotive sales",
        "cost of revenues",
    ],
    "us-gaap:ResearchAndDevelopmentExpense": [
        "research and development",
        "r&d",
    ],
    "us-gaap:SellingGeneralAndAdministrativeExpense": [
        "selling, general and administrative",
        "selling general and administrative",
        "sg&a",
    ],
}

# Regex to match dollar values in table cells: e.g. "49,571" or "65,121"
# or "(49,571)" for negatives.  Captures optional parentheses (negative).
_TABLE_VALUE_RE = re.compile(
    r"\(?\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*\)?",
)


class EvidenceLinker:
    """Link evidence items around shared periods and metrics.

    Given an :class:`EvidenceBundle` from hybrid retrieval, the linker
    enriches it by:

    1. Grouping chunks by their parent filing's ``period_end`` date.
    2. For each period, pulling in fact records that share the same
       period and any required concepts.
    3. For each period, pulling in table chunks from the same filing
       that mention matching metrics.
    4. **Table fallback** (Phase C): when XBRL facts are missing for a
       required concept, scan table chunks for matching line items and
       create table-backed :class:`FactRecord` instances.

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
        period_semantics: dict[str, PeriodSemantics] | None = None,
        original_query: str | None = None,
        semantic_scope: SemanticScope | None = None,
    ) -> EvidenceBundle:
        """Enrich an evidence bundle by linking across periods and metrics.

        Args:
            bundle: The raw evidence bundle from hybrid retrieval.
            required_concepts: XBRL concepts to look up in the facts store.
            required_periods: If given, restrict linking to these periods.
            period_semantics: Optional period semantics map from the plan.
            original_query: Original user query for context-sensitive alias matching.

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
                            tbl,
                            required_concepts,
                            original_query=original_query,
                            semantic_scope=semantic_scope,
                        ):
                            continue
                        table_chunks.append(tbl)
                        existing_chunk_ids.add(tbl.chunk_id)
                        scores[str(tbl.chunk_id)] = 0.0  # linked, not retrieved

        # --- Phase C: Table fallback ---
        # After normal linking, check for missing concepts and attempt to
        # extract values from table chunks.
        table_fallback_count = 0
        table_fallback_details: list[dict[str, object]] = []
        if required_concepts and required_periods:
            for period in required_periods:
                for concept in required_concepts:
                    # Skip concepts already resolved via XBRL
                    if any(f.concept == concept and f.period_end == period for f in facts):
                        continue
                    # Attempt table fallback
                    fallback = self._try_table_fallback(
                        concept,
                        period,
                        table_chunks,
                        original_query=original_query,
                        semantic_scope=semantic_scope,
                    )
                    if fallback is not None:
                        fallback_fact, fallback_detail = fallback
                        facts.append(fallback_fact)
                        existing_fact_ids.add(fallback_fact.fact_id)
                        table_fallback_count += 1
                        table_fallback_details.append(fallback_detail)

        # Build per-period coverage for debug metadata
        period_coverage = self._compute_period_coverage(
            required_periods or [],
            required_concepts or [],
            facts,
            semantic_scope=semantic_scope,
        )
        # Determine which periods lack required evidence.
        # For concept-scoped queries, a period is missing unless *all* required
        # concepts are present (not just any fact in that period).
        missing_periods: list[str] = []
        missing_concepts_by_period: dict[str, list[str]] = {}
        for period in required_periods or []:
            key = period.isoformat()
            coverage = period_coverage.get(key, {})
            missing_concepts = list(coverage.get("missing_concepts", []))
            if missing_concepts:
                missing_concepts_by_period[key] = missing_concepts

            if required_concepts:
                if not coverage.get("has_required_concepts", False):
                    missing_periods.append(key)
            elif not coverage.get("has_facts", False):
                missing_periods.append(key)

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
                "table_fallback_count": table_fallback_count,
                "table_fallback_details": table_fallback_details,
                "period_coverage": period_coverage,
                "missing_periods": missing_periods,
                "missing_concepts_by_period": missing_concepts_by_period,
            },
        )

    # ------------------------------------------------------------------
    # Table fallback (Phase C)
    # ------------------------------------------------------------------

    def _try_table_fallback(
        self,
        concept: str,
        period: date,
        table_chunks: list[TableChunk],
        *,
        original_query: str | None = None,
        semantic_scope: SemanticScope | None = None,
    ) -> tuple[FactRecord, dict[str, object]] | None:
        """Attempt to extract a fact value from table chunks.

        Scans table chunks belonging to filings at *period* for rows
        that mention the concept (using human-readable aliases).
        Returns a table-backed :class:`FactRecord` on success, or
        ``None`` if no match is found.
        """
        aliases = self._get_table_aliases(
            concept,
            original_query=original_query,
            semantic_scope=semantic_scope,
        )
        if not aliases:
            return None

        candidate_tables: list[TableChunk] = []
        seen_chunk_ids: set[UUID] = set()
        for tbl in table_chunks:
            if tbl.chunk_id in seen_chunk_ids:
                continue
            candidate_tables.append(tbl)
            seen_chunk_ids.add(tbl.chunk_id)
        for filing in self._corpus.list_filings():
            for tbl in self._corpus.get_table_chunks(filing.doc_id):
                if tbl.chunk_id in seen_chunk_ids:
                    continue
                candidate_tables.append(tbl)
                seen_chunk_ids.add(tbl.chunk_id)

        for tbl in candidate_tables:
            match = self._extract_value_from_table(tbl, aliases, period=period)
            if match is None:
                continue
            value, matched_alias, column_index = match
            if value is not None:
                # Tesla tables typically report in millions
                # We need to determine the scale from table context
                scale = self._infer_table_scale(tbl)
                fact = FactRecord(
                    fact_id=uuid4(),
                    doc_id=tbl.doc_id,
                    concept=concept,
                    label=f"{matched_alias} (table-extracted)",
                    value=value,
                    unit="USD",
                    scale=scale,
                    period_start=date(period.year, 1, 1),
                    period_end=period,
                    is_instant=False,
                    source_chunk_id=tbl.chunk_id,
                )
                detail = {
                    "concept": concept,
                    "requested_period": period.isoformat(),
                    "matched_alias": matched_alias,
                    "source_chunk_id": str(tbl.chunk_id),
                    "source_doc_id": str(tbl.doc_id),
                    "scale": scale,
                    "raw_value": value,
                    "column_index": column_index,
                    "semantic_scope": (
                        semantic_scope.value if semantic_scope is not None else "general"
                    ),
                }
                return fact, detail

        candidate_sections: list[SectionChunk] = []
        seen_section_ids: set[UUID] = set()
        for filing in self._corpus.list_filings():
            for section in self._corpus.get_section_chunks(filing.doc_id):
                if section.chunk_id in seen_section_ids:
                    continue
                candidate_sections.append(section)
                seen_section_ids.add(section.chunk_id)

        for section in candidate_sections:
            match = self._extract_value_from_text_block(section.text, aliases, period=period)
            if match is None:
                continue
            value, matched_alias, column_index = match
            scale = self._infer_text_scale(section.text)
            fact = FactRecord(
                fact_id=uuid4(),
                doc_id=section.doc_id,
                concept=concept,
                label=f"{matched_alias} (text-extracted)",
                value=value,
                unit="USD",
                scale=scale,
                period_start=date(period.year, 1, 1),
                period_end=period,
                is_instant=False,
                source_chunk_id=section.chunk_id,
            )
            detail = {
                "concept": concept,
                "requested_period": period.isoformat(),
                "matched_alias": matched_alias,
                "source_chunk_id": str(section.chunk_id),
                "source_doc_id": str(section.doc_id),
                "source_kind": "section_text",
                "scale": scale,
                "raw_value": value,
                "column_index": column_index,
                "semantic_scope": (
                    semantic_scope.value if semantic_scope is not None else "general"
                ),
            }
            return fact, detail
        return None

    @staticmethod
    def _get_table_aliases(
        concept: str,
        *,
        original_query: str | None = None,
        semantic_scope: SemanticScope | None = None,
    ) -> list[str]:
        """Get human-readable aliases for table matching.

        Uses the configured alias map, falling back to camelCase
        splitting of the concept's local name.
        """
        if concept in _CONCEPT_TABLE_ALIASES:
            aliases = list(_CONCEPT_TABLE_ALIASES[concept])
            if (
                concept == "us-gaap:CostOfGoodsAndServicesSold"
                and (
                    semantic_scope == SemanticScope.AUTOMOTIVE
                    or (
                        original_query
                        and (
                            "automotive" in original_query.lower() or "汽车" in original_query
                        )
                    )
                )
            ):
                automotive_aliases = [a for a in aliases if "automotive" in a or "汽车" in a]
                return automotive_aliases or aliases
            return aliases

        # Fallback: split camelCase
        label = concept.split(":")[-1] if ":" in concept else concept
        words: list[str] = []
        current: list[str] = []
        for ch in label:
            if ch.isupper() and current:
                words.append("".join(current).lower())
                current = [ch]
            else:
                current.append(ch)
        if current:
            words.append("".join(current).lower())
        human_label = " ".join(words)
        return [human_label]

    @staticmethod
    def _extract_value_from_table(
        table: TableChunk,
        aliases: list[str],
        *,
        period: date | None = None,
    ) -> tuple[float, str, int | None] | None:
        """Extract a numeric value from a table chunk matching any alias.

        Searches row-by-row for a row whose first cell matches one of
        the aliases, then returns the matching numeric value.
        """
        for row in table.rows:
            if not row:
                continue
            row_label = row[0].strip().lower()
            matched_alias = next((alias for alias in aliases if alias.lower() in row_label), None)
            if matched_alias is None:
                # Also check if the alias appears in a combined row text
                row_text = " ".join(cell.strip().lower() for cell in row)
                matched_alias = next(
                    (alias for alias in aliases if alias.lower() in row_text),
                    None,
                )

            if matched_alias:
                target_column = EvidenceLinker._column_index_for_period(table, row, period)
                if target_column is not None and target_column < len(row):
                    parsed = EvidenceLinker._parse_table_cell_value(row[target_column])
                    if parsed is not None:
                        return parsed, matched_alias, target_column

                numeric_cells: list[tuple[int, float]] = []
                for index, cell in enumerate(row[1:], start=1):
                    parsed = EvidenceLinker._parse_table_cell_value(cell)
                    if parsed is not None:
                        numeric_cells.append((index, parsed))

                if period is None and numeric_cells:
                    index, parsed = numeric_cells[0]
                    return parsed, matched_alias, index

                if len(numeric_cells) == 1:
                    index, parsed = numeric_cells[0]
                    return parsed, matched_alias, index
        return None

    @staticmethod
    def _parse_table_cell_value(cell: str) -> float | None:
        """Parse a numeric table cell, preserving negatives in parentheses."""
        stripped = cell.strip()
        if not stripped:
            return None
        match = _TABLE_VALUE_RE.search(stripped)
        if match is None:
            return None
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return None
        if stripped.startswith("(") and stripped.endswith(")"):
            return -value
        if stripped.startswith("-"):
            return -value
        return value

    @staticmethod
    def _extract_value_from_text_block(
        text: str,
        aliases: list[str],
        *,
        period: date | None = None,
    ) -> tuple[float, str, int | None] | None:
        """Extract a period-scoped numeric value from a text chunk containing table text."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        target_column = EvidenceLinker._text_column_index_for_period(lines, period)
        for line in lines:
            lowered = line.lower()
            matched_alias = next((alias for alias in aliases if alias.lower() in lowered), None)
            if matched_alias is None:
                continue
            tokens = _TABLE_VALUE_RE.findall(line)
            numbers = [EvidenceLinker._parse_table_cell_value(token) for token in tokens]
            parsed_numbers = [value for value in numbers if value is not None]
            if target_column is not None and target_column < len(parsed_numbers):
                return parsed_numbers[target_column], matched_alias, target_column
            if len(parsed_numbers) == 1:
                return parsed_numbers[0], matched_alias, 0
        return None

    @staticmethod
    def _text_column_index_for_period(lines: list[str], period: date | None) -> int | None:
        """Infer the numeric column index for a period from plain-text table lines."""
        if period is None:
            return None
        target_year = str(period.year)
        for line in lines[:12]:
            years = re.findall(r"20\d{2}", line)
            if target_year in years:
                return years.index(target_year)
        return None

    @staticmethod
    def _infer_text_scale(text: str) -> int:
        """Infer numeric scale from a text block that contains table content."""
        lowered = text.lower()
        if "in millions" in lowered:
            return 1_000_000
        if "in thousands" in lowered:
            return 1_000
        if "in billions" in lowered:
            return 1_000_000_000
        return 1

    @staticmethod
    def _column_index_for_period(
        table: TableChunk,
        row: list[str],
        period: date | None,
    ) -> int | None:
        """Return the row cell index that matches the requested period."""
        if period is None:
            return None

        target_year = str(period.year)
        header_sources: list[tuple[list[str], bool]] = []
        if table.headers:
            header_sources.append((table.headers, False))
        if table.rows:
            first_row = table.rows[0]
            if any(target_year in cell for cell in first_row):
                header_sources.append((first_row, True))

        for header_row, includes_label_column in header_sources:
            for index, cell in enumerate(header_row):
                searchable = cell.lower()
                if target_year not in searchable:
                    continue
                if includes_label_column:
                    return index
                return index + 1
        return None

    @staticmethod
    def _infer_table_scale(table: TableChunk) -> int:
        """Infer the numeric scale from a table's caption/headers.

        Tesla tables typically state "in millions" in the caption.
        Returns the multiplier (e.g. 1_000_000 for millions).
        """
        searchable = f"{table.caption} {' '.join(table.headers)}".lower()
        if "in millions" in searchable:
            return 1_000_000
        if "in thousands" in searchable:
            return 1_000
        if "in billions" in searchable:
            return 1_000_000_000
        # Default: assume raw values (no scale)
        return 1

    @staticmethod
    def _table_mentions_concept(
        table: TableChunk,
        concepts: list[str],
        *,
        original_query: str | None = None,
        semantic_scope: SemanticScope | None = None,
    ) -> bool:
        """Check if a table chunk's text mentions any of the concepts."""
        searchable = f"{table.caption} {table.raw_text} {' '.join(table.headers)}".lower()
        for concept in concepts:
            # Check against configured aliases first
            aliases = EvidenceLinker._get_table_aliases(
                concept,
                original_query=original_query,
                semantic_scope=semantic_scope,
            )
            for alias in aliases:
                if alias.lower() in searchable:
                    return True
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

    @staticmethod
    def _compute_period_coverage(
        required_periods: list[date],
        required_concepts: list[str],
        facts: list[FactRecord],
        *,
        semantic_scope: SemanticScope | None = None,
    ) -> dict[str, dict]:
        """Compute per-period evidence coverage for debug metadata.

        Returns a dict mapping ISO date strings to coverage info:
        ``{"has_facts": bool, "concept_count": int, "matched_concepts": [...]}``.
        """
        coverage: dict[str, dict] = {}
        for period in required_periods:
            period_facts = [f for f in facts if f.period_end == period]
            if required_concepts:
                matched_concepts = [
                    concept
                    for concept in required_concepts
                    if EvidenceLinker._period_has_concept(
                        period_facts,
                        concept,
                        semantic_scope=semantic_scope,
                    )
                ]
                missing_concepts = sorted(set(required_concepts) - set(matched_concepts))
            else:
                matched_concepts = sorted({f.concept for f in period_facts})
                missing_concepts = []
            coverage[period.isoformat()] = {
                "has_facts": len(period_facts) > 0,
                "fact_count": len(period_facts),
                "concept_count": len(matched_concepts),
                "matched_concepts": sorted(matched_concepts),
                "missing_concepts": missing_concepts,
                "has_required_concepts": len(missing_concepts) == 0,
            }
        return coverage

    @staticmethod
    def _period_has_concept(
        period_facts: list[FactRecord],
        required_concept: str,
        *,
        semantic_scope: SemanticScope | None = None,
    ) -> bool:
        """Return whether a period has a concept under the current semantic scope."""
        available = {fact.concept for fact in period_facts}
        if required_concept in available:
            return True
        if semantic_scope == SemanticScope.AUTOMOTIVE:
            return False
        if required_concept == "us-gaap:CostOfGoodsAndServicesSold":
            return "us-gaap:CostOfRevenue" in available
        return False
