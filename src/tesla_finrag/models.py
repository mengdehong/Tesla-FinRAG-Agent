"""Canonical typed domain contracts for the Tesla FinRAG system.

All ingestion, retrieval, calculation, and answer-generation subsystems
import from this module rather than defining their own payload shapes.
Additive field extensions are allowed in later changes; semantic renames are not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared enumerations
# ---------------------------------------------------------------------------


class FilingType(StrEnum):
    """SEC filing form types supported by the system."""

    ANNUAL = "10-K"
    QUARTERLY = "10-Q"


class FilingAvailability(StrEnum):
    """Status of a filing source in the manifest."""

    AVAILABLE = "available"
    DOWNLOADABLE = "downloadable"
    MISSING = "missing"


class ChunkKind(StrEnum):
    """Discriminator for chunk variants stored in the corpus."""

    SECTION = "section"
    TABLE = "table"


class AnswerStatus(StrEnum):
    """High-level outcome of an answer generation attempt."""

    OK = "ok"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CALCULATION_ERROR = "calculation_error"
    OUT_OF_SCOPE = "out_of_scope"


class QueryType(StrEnum):
    """Classification of user question intent for retrieval routing."""

    NARRATIVE_COMPARE = "narrative_compare"
    TABLE_LOOKUP = "table_lookup"
    NUMERIC_CALCULATION = "numeric_calculation"
    HYBRID_REASONING = "hybrid_reasoning"


class SearchMode(StrEnum):
    """Which search strategy produced a retrieval result."""

    LEXICAL = "lexical"
    VECTOR = "vector"
    HYBRID = "hybrid"
    FACT = "fact"


# ---------------------------------------------------------------------------
# Retrieval result
# ---------------------------------------------------------------------------


class RetrievalResult(BaseModel):
    """A single search result from any retrieval strategy."""

    chunk_id: UUID
    doc_id: UUID
    content: str
    score: float
    source: SearchMode
    chunk_type: ChunkKind
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Filing metadata
# ---------------------------------------------------------------------------


class FilingDocument(BaseModel):
    """Metadata record for a single Tesla SEC filing.

    The ``doc_id`` is the stable key used throughout all downstream tables.
    """

    doc_id: UUID = Field(default_factory=uuid4)
    ticker: str = Field("TSLA", description="Equity ticker symbol.")
    filing_type: FilingType
    period_end: date = Field(description="Last day of the reported fiscal period.")
    fiscal_year: int
    fiscal_quarter: int | None = Field(
        None,
        description="1-4 for quarterly filings; None for annual.",
    )
    accession_number: str = Field(description="SEC EDGAR accession number.")
    filed_at: date
    source_path: str = Field(description="Relative path inside data/raw/.")

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Filing manifest
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel):
    """A single target filing and its availability status."""

    ticker: str = "TSLA"
    filing_type: FilingType
    fiscal_year: int
    fiscal_quarter: int | None = Field(None, description="1-3 for 10-Q; None for 10-K.")
    period_end: date = Field(description="Last day of the reported fiscal period.")
    status: FilingAvailability = FilingAvailability.MISSING
    source_path: str | None = Field(
        None, description="Relative path inside data/raw/ when available."
    )
    notes: str = ""

    model_config = {"frozen": True}


class FilingManifest(BaseModel):
    """Complete target filing inventory with gap reporting."""

    entries: list[ManifestEntry] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def available(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.status == FilingAvailability.AVAILABLE]

    @property
    def gaps(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.status != FilingAvailability.AVAILABLE]

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def available_count(self) -> int:
        return len(self.available)

    @property
    def gap_count(self) -> int:
        return len(self.gaps)


# ---------------------------------------------------------------------------
# Corpus chunks
# ---------------------------------------------------------------------------


class ChunkBase(BaseModel):
    """Fields shared by all chunk variants."""

    chunk_id: UUID = Field(default_factory=uuid4)
    doc_id: UUID = Field(description="Parent FilingDocument.doc_id.")
    kind: ChunkKind
    page_number: int | None = None
    char_offset: int | None = None

    model_config = {"frozen": True}


class SectionChunk(ChunkBase):
    """A contiguous narrative passage extracted from a filing section."""

    kind: ChunkKind = ChunkKind.SECTION
    section_title: str
    text: str
    token_count: int = Field(ge=0)


class TableChunk(ChunkBase):
    """A financial table extracted from a filing."""

    kind: ChunkKind = ChunkKind.TABLE
    section_title: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    raw_text: str = Field(description="Serialised fallback text for embedding.")


# ---------------------------------------------------------------------------
# Financial facts
# ---------------------------------------------------------------------------


class FactRecord(BaseModel):
    """A single normalised financial fact extracted from XBRL or table parsing.

    ``period_start`` is None for instant facts (e.g. balance sheet items).
    """

    fact_id: UUID = Field(default_factory=uuid4)
    doc_id: UUID
    concept: str = Field(description="XBRL concept name, e.g. 'us-gaap:Revenue'.")
    label: str = Field(description="Human-readable label for display.")
    value: float
    unit: str = Field(description="e.g. 'USD', 'shares', 'pure'.")
    scale: int = Field(1, description="Multiplier applied to raw value (e.g. 1000).")
    period_start: date | None = None
    period_end: date
    is_instant: bool = False
    source_chunk_id: UUID | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Query planning
# ---------------------------------------------------------------------------


class QueryPlan(BaseModel):
    """Structured representation of a decomposed user question.

    Produced by the query-planning service before retrieval begins.
    """

    plan_id: UUID = Field(default_factory=uuid4)
    original_query: str
    query_type: QueryType = QueryType.HYBRID_REASONING
    sub_questions: list[str] = Field(default_factory=list)
    retrieval_keywords: list[str] = Field(
        default_factory=list,
        description="Explicit keywords for lexical search.",
    )
    required_periods: list[date] = Field(
        default_factory=list,
        description="Fiscal period-end dates the question explicitly references.",
    )
    required_concepts: list[str] = Field(
        default_factory=list,
        description="XBRL concept names identified in the question.",
    )
    needs_calculation: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Evidence bundle
# ---------------------------------------------------------------------------


class EvidenceBundle(BaseModel):
    """Retrieved evidence assembled for a single query plan.

    Passed from the retrieval layer to the answer-generation service.
    """

    bundle_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    section_chunks: list[SectionChunk] = Field(default_factory=list)
    table_chunks: list[TableChunk] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)
    retrieval_scores: dict[str, float] = Field(
        default_factory=dict,
        description="chunk_id (str) -> relevance score.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Answer payload
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A single source citation attached to an answer."""

    chunk_id: UUID
    doc_id: UUID
    filing_type: FilingType
    period_end: date
    excerpt: str = ""


class AnswerPayload(BaseModel):
    """Final structured answer returned to the user or UI layer."""

    answer_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    status: AnswerStatus
    answer_text: str
    citations: list[Citation] = Field(default_factory=list)
    calculation_trace: list[str] = Field(
        default_factory=list,
        description="Step-by-step arithmetic used, if applicable.",
    )
    confidence: float | None = Field(
        None, ge=0.0, le=1.0, description="Optional model confidence score."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
