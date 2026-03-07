"""Pydantic models for the evaluation framework.

Covers benchmark questions, per-question results, failure analyses,
and full evaluation run summaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

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
# Benchmark questions
# ---------------------------------------------------------------------------


class BenchmarkQuestion(BaseModel):
    """A single complex evaluation question with expected output metadata."""

    question_id: str
    question: str
    category: QuestionCategory
    difficulty: Difficulty
    expected_answer_contains: list[str] = Field(
        default_factory=list,
        description="Key phrases the answer must contain to be considered correct.",
    )
    required_periods: list[str] = Field(
        default_factory=list,
        description="Fiscal period end dates, e.g. '2023-12-31'.",
    )
    required_concepts: list[str] = Field(
        default_factory=list,
        description="XBRL concepts the answer should reference.",
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


# ---------------------------------------------------------------------------
# Evaluation run results
# ---------------------------------------------------------------------------


class QuestionResult(BaseModel):
    """Outcome of running a single benchmark question."""

    question_id: str
    answer_status: str = Field(description="AnswerStatus value from the pipeline.")
    answer_text: str
    latency_ms: float = Field(ge=0)
    passed: bool
    notes: str = ""


class RunSummary(BaseModel):
    """Aggregate statistics for an evaluation run."""

    total: int
    pass_count: int
    fail_count: int
    error_count: int
    avg_latency_ms: float
    pass_rate: float = Field(ge=0.0, le=1.0, description="Fraction of questions that passed.")


class EvaluationRun(BaseModel):
    """Full evaluation run record, persisted as a JSON artifact."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_questions: int
    results: list[QuestionResult] = Field(default_factory=list)
    summary: RunSummary
