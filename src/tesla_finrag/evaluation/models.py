"""Pydantic models for the evaluation framework.

Covers benchmark questions, per-question results, failure analyses,
and full evaluation run summaries.

Includes structured assertion models for dual-track evaluation
(legacy keyword judge + structured assertion judge).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from tesla_finrag.models import AnswerStatus

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class QuestionCategory(StrEnum):
    """Classification of benchmark question complexity."""

    CROSS_YEAR = "cross_year"
    CALCULATION = "calculation"
    TEXT_PLUS_TABLE = "text_plus_table"
    TIME_SEQUENCED = "time_sequenced"
    MULTI_PERIOD = "multi_period"
    BALANCE_SHEET = "balance_sheet"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class ResultStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Structured assertion models
# ---------------------------------------------------------------------------


class CalcOperation(StrEnum):
    """Allowed calculation operation types for structured assertions."""

    LOOKUP = "lookup"
    RATIO = "ratio"
    PCT_CHANGE = "pct_change"
    DIFFERENCE = "difference"
    RANK = "rank"


class ExpectedCalc(BaseModel):
    """Structured numeric assertion for a benchmark question.

    Defines the expected calculation operation, expected numeric result,
    and tolerance for validation. Used by the structured judge.
    """

    operation: CalcOperation = Field(
        description="Expected calculation type.",
    )
    numerator: str | None = Field(
        None,
        description="XBRL concept for the numerator (ratio operations).",
    )
    denominator: str | None = Field(
        None,
        description="XBRL concept for the denominator (ratio operations).",
    )
    expected_value: float | None = Field(
        None,
        description="Expected numeric result value.",
    )
    tolerance: float = Field(
        0.01,
        description="Relative tolerance for numeric comparison (e.g. 0.01 = 1%).",
    )


class JudgeBreakdown(BaseModel):
    """Diagnostic breakdown of structured judge evaluation.

    Records which assertions passed or failed so operators can
    distinguish system errors from judge configuration issues.
    """

    status_ok: bool = Field(description="Whether the answer status matched expectations.")
    facts_found: list[str] = Field(
        default_factory=list,
        description="Expected XBRL concepts that were found in the answer evidence.",
    )
    facts_missing: list[str] = Field(
        default_factory=list,
        description="Expected XBRL concepts that were NOT found in the answer evidence.",
    )
    period_facts_ok: bool | None = Field(
        None,
        description=(
            "Whether required concepts were covered for every required period. "
            "None when period-level checks are not applicable."
        ),
    )
    periods_missing_facts: list[str] = Field(
        default_factory=list,
        description="Required periods that failed fact-coverage checks.",
    )
    facts_missing_by_period: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-period mapping of expected concepts that were missing.",
    )
    calc_correct: bool | None = Field(
        None,
        description=(
            "Whether the numeric result matched within tolerance. None if no calc assertion."
        ),
    )
    calc_detail: str = Field(
        "",
        description="Human-readable explanation of the numeric comparison result.",
    )
    operation_ok: bool | None = Field(
        None,
        description=(
            "Whether expected_calc.operation matched retrieval_debug.calculation_intent. "
            "None when no expected_calc is configured."
        ),
    )
    operation_detail: str = Field(
        "",
        description="Human-readable explanation of operation-intent validation.",
    )
    narrative_terms_found: list[str] = Field(
        default_factory=list,
        description="Expected narrative cue terms that were found in the answer text.",
    )
    narrative_terms_missing: list[str] = Field(
        default_factory=list,
        description="Expected narrative cue terms that were NOT found in the answer text.",
    )


# ---------------------------------------------------------------------------
# Benchmark questions
# ---------------------------------------------------------------------------


class BenchmarkQuestion(BaseModel):
    """A single complex evaluation question with expected output metadata.

    Supports dual-track evaluation: legacy keyword matching via
    ``expected_answer_contains`` and structured assertions via
    ``expected_status``, ``expected_facts``, and ``expected_calc``.
    """

    question_id: str
    question: str
    category: QuestionCategory
    difficulty: Difficulty
    expected_answer_contains: list[str] = Field(
        default_factory=list,
        description="Key phrases the answer must contain to be considered correct (legacy judge).",
    )
    required_periods: list[str] = Field(
        default_factory=list,
        description="Fiscal period end dates, e.g. '2023-12-31'.",
    )
    required_concepts: list[str] = Field(
        default_factory=list,
        description="XBRL concepts the answer should reference.",
    )
    # --- Structured assertion fields (Phase A) ---
    expected_status: AnswerStatus | None = Field(
        None,
        description="Expected answer status (e.g. 'ok'). Used by structured judge.",
    )
    expected_facts: list[str] = Field(
        default_factory=list,
        description="XBRL concepts expected to appear in the answer evidence.",
    )
    expected_calc: ExpectedCalc | None = Field(
        None,
        description="Structured numeric assertion for calculation validation.",
    )
    expected_period_semantics: dict[str, str] = Field(
        default_factory=dict,
        description="ISO date string -> expected period semantics classification.",
    )
    expected_narrative_terms: list[str] = Field(
        default_factory=list,
        description="Narrative cue terms that should appear in the final answer text.",
    )

    @property
    def has_structured_assertions(self) -> bool:
        """Return True if any structured assertion field is populated.

        The structured judge should run whenever this returns True,
        rather than checking only ``expected_status``.
        """
        return (
            self.expected_status is not None
            or len(self.expected_facts) > 0
            or self.expected_calc is not None
            or len(self.expected_narrative_terms) > 0
        )


# ---------------------------------------------------------------------------
# Failure analysis
# ---------------------------------------------------------------------------


class FailureAnalysis(BaseModel):
    """Structured record of a failed or low-quality answer."""

    case_id: str
    question_id: str
    question: str
    expected_answer: str
    actual_answer: str
    symptom: str = Field(description="User-visible problem description.")
    retrieval_breakdown: str = Field(description="What the retrieval system did wrong.")
    root_cause: str = Field(description="Why the system produced this output.")
    mitigation: str = Field(description="Concrete improvement direction.")
    severity: Severity
    baseline_run_id: str = Field(
        description="Run ID of the baseline from which this analysis was derived.",
    )

    @field_validator("baseline_run_id")
    @classmethod
    def _validate_baseline_run_id(cls, value: str) -> str:
        run_id = value.strip()
        if not run_id:
            msg = "baseline_run_id must not be blank"
            raise ValueError(msg)
        return run_id


# ---------------------------------------------------------------------------
# Evaluation run results
# ---------------------------------------------------------------------------


class QuestionResult(BaseModel):
    """Outcome of running a single benchmark question.

    Supports dual-track evaluation with ``legacy_passed`` and
    ``structured_passed`` fields. The primary ``passed`` field is
    determined by ``structured_passed`` when available, falling back
    to legacy judge otherwise.
    """

    question_id: str
    answer_status: AnswerStatus | ResultStatus = Field(
        description="AnswerStatus or runner error state for the question."
    )
    answer_text: str
    latency_ms: float = Field(ge=0)
    passed: bool
    notes: str = ""
    # --- Dual-track evaluation fields (Phase A) ---
    legacy_passed: bool | None = Field(
        None,
        description="Result from legacy keyword-contains judge.",
    )
    structured_passed: bool | None = Field(
        None,
        description="Result from structured assertion judge.",
    )
    judge_breakdown: JudgeBreakdown | None = Field(
        None,
        description="Diagnostic breakdown of structured judge evaluation.",
    )
    retrieval_debug: dict[str, Any] = Field(
        default_factory=dict,
        description="Full retrieval and planning diagnostics from the answer pipeline.",
    )


class RunSummary(BaseModel):
    """Aggregate statistics for an evaluation run."""

    total: int
    pass_count: int
    fail_count: int
    error_count: int
    avg_latency_ms: float
    pass_rate: float = Field(ge=0.0, le=1.0, description="Fraction of questions that passed.")

    @model_validator(mode="after")
    def _recompute_pass_rate(self) -> RunSummary:
        self.pass_rate = round(self.pass_count / self.total, 4) if self.total > 0 else 0.0
        return self


class EvaluationRun(BaseModel):
    """Full evaluation run record, persisted as a JSON artifact."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_questions: int
    results: list[QuestionResult] = Field(default_factory=list)
    summary: RunSummary


# ---------------------------------------------------------------------------
# Latest baseline pointer
# ---------------------------------------------------------------------------


class BaselineSummary(BaseModel):
    """Stable pointer to the latest accepted evaluation baseline.

    Persisted as ``data/evaluation/latest_baseline.json`` so operators
    can discover current benchmark status without inspecting individual
    run files.
    """

    run_id: str = Field(description="ID of the accepted baseline run.")
    timestamp: datetime = Field(description="When the baseline run was executed.")
    run_file: str = Field(
        description="Relative path from the project root to the underlying run JSON."
    )
    summary: RunSummary = Field(description="Top-line metrics snapshot.")
    question_pass_fail: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-question pass/fail map keyed by question_id.",
    )
