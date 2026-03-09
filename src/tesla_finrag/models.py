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


class ValidationStatus(StrEnum):
    """Outcome of numeric / structural validation on an extracted artifact."""

    VALID = "valid"
    SUSPECT = "suspect"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


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


class PeriodSemantics(StrEnum):
    """Temporal classification of a financial fact or query period."""

    ANNUAL_CUMULATIVE = "annual_cumulative"
    QUARTERLY_STANDALONE = "quarterly_standalone"
    DERIVED_STANDALONE = "derived_standalone"
    INSTANT = "instant"
    UNKNOWN = "unknown"


class SearchMode(StrEnum):
    """Which search strategy produced a retrieval result."""

    LEXICAL = "lexical"
    VECTOR = "vector"
    HYBRID = "hybrid"
    FACT = "fact"


class QueryLanguage(StrEnum):
    """Detected language family for the user query."""

    ENGLISH = "english"
    CHINESE = "chinese"
    MIXED = "mixed"


class SemanticScope(StrEnum):
    """Optional business scope attached to a query or sub-query."""

    GENERAL = "general"
    AUTOMOTIVE = "automotive"


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
        description="1-3 for quarterly filings; None for annual.",
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
# Parser provenance & validation
# ---------------------------------------------------------------------------


class ParserProvenance(BaseModel):
    """Records which parser path produced an extracted artifact.

    Attached to every chunk so downstream consumers and operators can trace
    how a narrative or table artifact was produced, including any fallback.
    """

    parser_name: str = Field(
        "pdfplumber",
        description="Primary parser that produced the artifact (e.g. 'pdfplumber', 'pymupdf').",
    )
    used_fallback: bool = Field(
        False,
        description="Whether a fallback parser was used instead of the primary.",
    )
    fallback_reason: str | None = Field(
        None,
        description="Why the fallback was triggered (e.g. 'empty_text', 'no_tables').",
    )

    model_config = {"frozen": True}


class CellValidationResult(BaseModel):
    """Per-cell validation outcome for a financial table row.

    Stored alongside table chunks so downstream consumers can distinguish
    trusted numeric evidence from suspect or failed extractions.
    """

    row_index: int = Field(ge=0)
    col_index: int = Field(ge=0)
    raw_value: str = Field(description="Original cell text before normalization.")
    normalized_value: float | None = Field(
        None,
        description="Parsed numeric value after normalization, if successful.",
    )
    status: ValidationStatus = ValidationStatus.NOT_CHECKED
    detail: str = Field(
        "",
        description="Human-readable explanation (e.g. 'OCR substitution detected').",
    )

    model_config = {"frozen": True}


class FactReconciliationResult(BaseModel):
    """Outcome of reconciling a table-derived value against an authoritative XBRL fact."""

    concept: str = Field(description="XBRL concept matched (e.g. 'us-gaap:Revenues').")
    period_end: date
    table_value: float
    fact_value: float
    tolerance: float = Field(
        0.01,
        description="Relative tolerance used for the comparison.",
    )
    matched: bool = Field(
        description="True when table and fact values agree within tolerance.",
    )
    detail: str = ""

    model_config = {"frozen": True}


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
    parser_provenance: ParserProvenance | None = Field(
        None,
        description="Parser path and fallback info that produced this chunk.",
    )


class TableChunk(ChunkBase):
    """A financial table extracted from a filing."""

    kind: ChunkKind = ChunkKind.TABLE
    section_title: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    raw_text: str = Field(description="Serialised fallback text for embedding.")
    parser_provenance: ParserProvenance | None = Field(
        None,
        description="Parser path and fallback info that produced this chunk.",
    )
    validation_status: ValidationStatus = Field(
        ValidationStatus.NOT_CHECKED,
        description="Overall validation outcome for this table.",
    )
    cell_validations: list[CellValidationResult] = Field(
        default_factory=list,
        description="Per-cell validation results for numeric cells.",
    )
    fact_reconciliations: list[FactReconciliationResult] = Field(
        default_factory=list,
        description="Results of reconciling table values against authoritative XBRL facts.",
    )


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


class SubQuery(BaseModel):
    """A decomposed retrieval unit targeting a specific period and concept set.

    Multi-period and comparison questions are split into sub-queries so
    each required period can be retrieved and validated independently
    before the final answer assembly step.
    """

    sub_query_id: UUID = Field(default_factory=uuid4)
    text: str = Field(description="Natural-language retrieval query for this unit.")
    search_text: str = Field(
        "",
        description="Normalized retrieval query actually used by search backends.",
    )
    target_period: date | None = Field(
        None,
        description="Fiscal period-end this sub-query targets.",
    )
    target_concepts: list[str] = Field(
        default_factory=list,
        description="XBRL concepts to retrieve for this sub-query.",
    )
    period_semantics: PeriodSemantics = PeriodSemantics.UNKNOWN
    semantic_scope: SemanticScope | None = Field(
        None,
        description="Optional business scope such as 'automotive'.",
    )

    model_config = {"frozen": True}


class CalculationIntent(StrEnum):
    """Explicit calculation type the planner infers from the question.

    Used by the composer to route to the correct calculation method
    instead of relying on ``len(required_concepts)`` heuristics.
    """

    LOOKUP = "lookup"
    RATIO = "ratio"
    DIFFERENCE = "difference"
    PCT_CHANGE = "pct_change"
    RANK = "rank"
    STEP_TRACE = "step_trace"


class AnswerShape(StrEnum):
    """Expected shape of the final answer.

    Guides the composer's text-generation template selection.
    """

    SINGLE_VALUE = "single_value"
    COMPARISON = "comparison"
    RANKING = "ranking"
    COMPOSITE = "composite"
    TIME_SERIES = "time_series"


class CalculationOperand(BaseModel):
    """A single operand for a calculation (concept + role + period).

    Used by the planner to explicitly declare numerator/denominator
    or base/target for ratio and pct_change calculations, removing
    ambiguity from the composer.
    """

    concept: str = Field(
        description="XBRL concept name, e.g. 'us-gaap:GrossProfit'.",
    )
    role: str = Field(
        "primary",
        description=(
            "Role in the calculation: 'numerator', 'denominator', 'base', 'target', 'primary'."
        ),
    )
    period: date | None = Field(
        None,
        description="Specific period this operand targets (if relevant).",
    )

    model_config = {"frozen": True}


class QueryPlan(BaseModel):
    """Structured representation of a decomposed user question.

    Produced by the query-planning service before retrieval begins.
    """

    plan_id: UUID = Field(default_factory=uuid4)
    original_query: str
    query_language: QueryLanguage = QueryLanguage.ENGLISH
    normalized_query: str = Field(
        "",
        description="Normalized retrieval text used to search the filing corpus.",
    )
    query_type: QueryType = QueryType.HYBRID_REASONING
    semantic_scope: SemanticScope | None = Field(
        None,
        description="Optional business scope such as 'automotive'.",
    )
    sub_questions: list[str] = Field(default_factory=list)
    sub_queries: list[SubQuery] = Field(
        default_factory=list,
        description="Period-aware decomposed retrieval units for multi-period questions.",
    )
    retrieval_keywords: list[str] = Field(
        default_factory=list,
        description="Explicit keywords for lexical search.",
    )
    required_periods: list[date] = Field(
        default_factory=list,
        description="Fiscal period-end dates the question explicitly references.",
    )
    period_semantics: dict[str, PeriodSemantics] = Field(
        default_factory=dict,
        description="ISO date string -> period semantics classification.",
    )
    required_concepts: list[str] = Field(
        default_factory=list,
        description="XBRL concept names identified in the question.",
    )
    needs_calculation: bool = False
    # --- Phase B: Explicit calculation intent fields ---
    calculation_intent: CalculationIntent | None = Field(
        None,
        description=(
            "Explicit calculation type inferred from the question. "
            "Used by the composer for deterministic routing."
        ),
    )
    calculation_operands: list[CalculationOperand] = Field(
        default_factory=list,
        description=(
            "Ordered operands for the calculation. "
            "E.g. ratio: [numerator, denominator]; "
            "pct_change: [base, target]."
        ),
    )
    requires_step_trace: bool = Field(
        False,
        description=(
            "When True, the composer must show step-by-step calculation breakdown in the answer."
        ),
    )
    answer_shape: AnswerShape | None = Field(
        None,
        description="Expected shape of the final answer.",
    )
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
    section_title: str | None = Field(
        None,
        description="Source section or item title (e.g. 'Item 7: MD&A').",
    )
    page_number: int | None = Field(
        None,
        description="Page number in the source filing, if available.",
    )


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
    retrieval_debug: dict[str, Any] = Field(
        default_factory=dict,
        description="Retrieval and planning diagnostics for debugging and evaluation.",
    )
    confidence: float | None = Field(
        None, ge=0.0, le=1.0, description="Optional model confidence score."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
