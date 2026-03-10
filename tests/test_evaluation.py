"""Tests for the evaluation framework.

Covers benchmark question loading, failure analysis loading,
evaluation runner mechanics, and the demo response contract.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

import tesla_finrag.evaluation.runner as runner_module
from tesla_finrag.evaluation.models import (
    BaselineSummary,
    BenchmarkQuestion,
    CalcOperation,
    Difficulty,
    EvaluationRun,
    ExpectedCalc,
    FailureAnalysis,
    QuestionCategory,
    ResultStatus,
    RunSummary,
    Severity,
)
from tesla_finrag.evaluation.runner import (
    EvaluationRunner,
    load_baseline,
    load_benchmark_questions,
    load_failure_analyses,
)
from tesla_finrag.evaluation.workbench import (
    FilingScope,
    WorkbenchPipeline,
    _seed_demo_repositories,
)
from tesla_finrag.i18n import response_language_directive
from tesla_finrag.models import (
    AnswerPayload,
    AnswerStatus,
    ChunkKind,
    Citation,
    EvidenceBundle,
    FactRecord,
    FilingType,
    QueryType,
    SectionChunk,
    TableChunk,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_QUESTIONS = [
    {
        "question_id": "TEST-001",
        "question": "What was Tesla's revenue in FY2023?",
        "category": "cross_year",
        "difficulty": "medium",
        "expected_answer_contains": ["revenue", "2023"],
        "required_periods": ["2023-12-31"],
        "required_concepts": ["us-gaap:Revenues"],
    },
    {
        "question_id": "TEST-002",
        "question": "What was Tesla's gross margin in FY2023?",
        "category": "calculation",
        "difficulty": "hard",
        "expected_answer_contains": ["gross", "margin"],
        "required_periods": ["2023-12-31"],
        "required_concepts": ["us-gaap:GrossProfit", "us-gaap:Revenues"],
    },
]


@pytest.fixture()
def benchmark_file(tmp_path: Path) -> Path:
    """Write sample benchmark questions to a temp JSON file."""
    p = tmp_path / "benchmark_questions.json"
    p.write_text(json.dumps(_SAMPLE_QUESTIONS), encoding="utf-8")
    return p


@pytest.fixture()
def failure_analyses_file(tmp_path: Path) -> Path:
    """Write a sample failure analysis to a temp JSON file."""
    data = [
        {
            "case_id": "FA-TEST-001",
            "question_id": "TEST-001",
            "question": "What was Tesla's revenue in FY2023?",
            "expected_answer": "Revenue was $96.77B.",
            "actual_answer": "Revenue data not found.",
            "symptom": "No revenue figure in output.",
            "retrieval_breakdown": "No chunks retrieved.",
            "root_cause": "Empty index.",
            "mitigation": "Index filings first.",
            "severity": "major",
            "baseline_run_id": "baseline-001",
        }
    ]
    p = tmp_path / "failure_analyses.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Benchmark question model tests
# ---------------------------------------------------------------------------


class TestBenchmarkQuestion:
    def test_parse_valid_question(self) -> None:
        q = BenchmarkQuestion.model_validate(_SAMPLE_QUESTIONS[0])
        assert q.question_id == "TEST-001"
        assert q.category == QuestionCategory.CROSS_YEAR
        assert q.difficulty == Difficulty.MEDIUM
        assert len(q.expected_answer_contains) == 2
        assert "us-gaap:Revenues" in q.required_concepts

    def test_enum_values(self) -> None:
        for cat in QuestionCategory:
            assert isinstance(cat.value, str)
        for diff in Difficulty:
            assert isinstance(diff.value, str)


# ---------------------------------------------------------------------------
# Failure analysis model tests
# ---------------------------------------------------------------------------


class TestFailureAnalysis:
    def test_parse_from_json(self, failure_analyses_file: Path) -> None:
        raw = json.loads(failure_analyses_file.read_text())
        analyses = [FailureAnalysis.model_validate(item) for item in raw]
        assert len(analyses) == 1
        fa = analyses[0]
        assert fa.case_id == "FA-TEST-001"
        assert fa.severity == Severity.MAJOR
        assert fa.baseline_run_id == "baseline-001"

    def test_load_failure_analyses_helper(self, failure_analyses_file: Path) -> None:
        analyses = load_failure_analyses(failure_analyses_file)
        assert len(analyses) == 1
        assert analyses[0].question_id == "TEST-001"
        assert analyses[0].baseline_run_id == "baseline-001"

    def test_baseline_run_id_is_required(self) -> None:
        with pytest.raises(ValidationError):
            FailureAnalysis(
                case_id="FA-TEST-001",
                question_id="TEST-001",
                question="What was Tesla's revenue in FY2023?",
                expected_answer="Revenue was $96.77B.",
                actual_answer="Revenue data not found.",
                symptom="No revenue figure in output.",
                retrieval_breakdown="No chunks retrieved.",
                root_cause="Empty index.",
                mitigation="Index filings first.",
                severity=Severity.MAJOR,
            )

    def test_blank_baseline_run_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FailureAnalysis(
                case_id="FA-TEST-001",
                question_id="TEST-001",
                question="What was Tesla's revenue in FY2023?",
                expected_answer="Revenue was $96.77B.",
                actual_answer="Revenue data not found.",
                symptom="No revenue figure in output.",
                retrieval_breakdown="No chunks retrieved.",
                root_cause="Empty index.",
                mitigation="Index filings first.",
                severity=Severity.MAJOR,
                baseline_run_id="   ",
            )

    def test_all_severity_levels(self) -> None:
        for sev in Severity:
            fa = FailureAnalysis(
                case_id=f"FA-{sev.value}",
                question_id="Q",
                question="q?",
                expected_answer="a",
                actual_answer="b",
                symptom="s",
                retrieval_breakdown="r",
                root_cause="c",
                mitigation="m",
                severity=sev,
                baseline_run_id="baseline-001",
            )
            assert fa.severity == sev


# ---------------------------------------------------------------------------
# Benchmark loading tests
# ---------------------------------------------------------------------------


class TestLoadBenchmark:
    def test_load_from_file(self, benchmark_file: Path) -> None:
        questions = load_benchmark_questions(benchmark_file)
        assert len(questions) == 2
        assert questions[0].question_id == "TEST-001"
        assert questions[1].question_id == "TEST-002"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_benchmark_questions(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# Evaluation runner tests
# ---------------------------------------------------------------------------


class TestEvaluationRunner:
    def _make_pipeline(
        self,
        status: AnswerStatus = AnswerStatus.OK,
        text: str = "Tesla revenue in 2023 was $96.77B. Gross margin was 18.2%.",
    ):
        """Return a pipeline callable that produces a fixed answer."""

        def pipeline(question: str) -> AnswerPayload:
            return AnswerPayload(
                plan_id=uuid4(),
                status=status,
                answer_text=text,
                confidence=0.85,
            )

        return pipeline

    def test_run_all_passes(self, benchmark_file: Path) -> None:
        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        assert run.total_questions == 2
        assert run.summary.total == 2
        # The fixed answer contains "revenue", "2023", "gross", "margin"
        assert run.summary.pass_count == 2
        assert run.summary.pass_rate == 1.0

    def test_run_with_failing_answers(self, benchmark_file: Path) -> None:
        runner = EvaluationRunner(
            pipeline=self._make_pipeline(text="No data available."),
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        assert run.summary.fail_count == 2
        assert run.summary.pass_rate == 0.0

    def test_run_with_error_pipeline(self, benchmark_file: Path) -> None:
        def broken_pipeline(question: str) -> AnswerPayload:
            raise RuntimeError("Pipeline crashed")

        runner = EvaluationRunner(
            pipeline=broken_pipeline,
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        assert run.summary.error_count == 2
        assert all(r.notes == "Pipeline crashed" for r in run.results)

    def test_save_run(self, benchmark_file: Path, tmp_path: Path) -> None:
        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        path = runner.save_run(run, output_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".json"
        # Verify round-trip
        loaded = EvaluationRun.model_validate_json(path.read_text())
        assert loaded.run_id == run.run_id
        assert loaded.total_questions == 2

    def test_default_pipeline_never_passes_insufficient_answers(self, benchmark_file: Path) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(corpus_repo=corpus_repo, facts_repo=facts_repo)
        runner = EvaluationRunner(
            pipeline=pipeline.answer_question,
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        assert all(
            not (r.passed and r.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE)
            for r in run.results
        )

    def test_latency_is_positive(self, benchmark_file: Path) -> None:
        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=benchmark_file,
        )
        run = runner.run_all()
        for r in run.results:
            assert r.latency_ms >= 0

    def test_keyword_match_requires_ok_status(self) -> None:
        question = BenchmarkQuestion.model_validate(_SAMPLE_QUESTIONS[0])
        answer = AnswerPayload(
            plan_id=uuid4(),
            status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            answer_text="Revenue in 2023 was mentioned, but evidence is missing.",
            confidence=0.0,
        )
        assert EvaluationRunner._legacy_check(question, answer) is False


# ---------------------------------------------------------------------------
# Structured judge tests
# ---------------------------------------------------------------------------


class TestStructuredJudge:
    """Verify _structured_check for all assertion axes and operation types."""

    @staticmethod
    def _make_answer(
        status: AnswerStatus = AnswerStatus.OK,
        text: str = "",
        calc_trace: list[str] | None = None,
        retrieved_concepts: list[str] | None = None,
        calc_intent: str | None = None,
        fact_concepts_by_period: dict[str, list[str]] | None = None,
        missing_periods: list[str] | None = None,
    ) -> AnswerPayload:
        debug: dict = {}
        if retrieved_concepts is not None:
            debug["retrieved_fact_concepts"] = retrieved_concepts
        if calc_intent is not None:
            debug["calculation_intent"] = calc_intent
        if fact_concepts_by_period is not None:
            debug["fact_concepts_by_period"] = fact_concepts_by_period
        if missing_periods is not None:
            debug["missing_periods"] = missing_periods
        return AnswerPayload(
            plan_id=uuid4(),
            status=status,
            answer_text=text,
            calculation_trace=calc_trace or [],
            retrieval_debug=debug,
        )

    @staticmethod
    def _make_question(**kwargs) -> BenchmarkQuestion:
        defaults = {
            "question_id": "SJ-TEST",
            "question": "test question",
            "category": "cross_year",
            "difficulty": "easy",
        }
        defaults.update(kwargs)
        return BenchmarkQuestion(**defaults)

    # --- No structured assertions → (None, None) fallback ---

    def test_no_structured_assertions_returns_none(self) -> None:
        q = self._make_question()
        answer = self._make_answer(text="some answer")
        passed, breakdown = EvaluationRunner._structured_check(q, answer)
        assert passed is None
        assert breakdown is None

    # --- Status assertion ---

    def test_status_ok_passes(self) -> None:
        q = self._make_question(expected_status="ok")
        answer = self._make_answer(status=AnswerStatus.OK, text="answer")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd is not None
        assert bd.status_ok is True

    def test_status_mismatch_fails(self) -> None:
        q = self._make_question(expected_status="ok")
        answer = self._make_answer(status=AnswerStatus.INSUFFICIENT_EVIDENCE, text="no data")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.status_ok is False

    # --- Facts assertion ---

    def test_facts_all_found_passes(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_facts=["us-gaap:Revenues", "us-gaap:GrossProfit"],
        )
        answer = self._make_answer(
            text="revenue answer",
            retrieved_concepts=["us-gaap:Revenues", "us-gaap:GrossProfit"],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.facts_found == ["us-gaap:Revenues", "us-gaap:GrossProfit"]
        assert bd.facts_missing == []

    def test_facts_missing_fails(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_facts=["us-gaap:Revenues", "us-gaap:GrossProfit"],
        )
        answer = self._make_answer(
            text="revenue answer",
            retrieved_concepts=["us-gaap:Revenues"],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.facts_found == ["us-gaap:Revenues"]
        assert bd.facts_missing == ["us-gaap:GrossProfit"]

    def test_equivalent_fact_concept_is_accepted(self) -> None:
        """CostOfRevenue should satisfy CostOfGoodsAndServicesSold assertion."""
        q = self._make_question(
            expected_status="ok",
            expected_facts=["us-gaap:CostOfGoodsAndServicesSold"],
        )
        answer = self._make_answer(
            text="cost answer",
            retrieved_concepts=["us-gaap:CostOfRevenue"],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.facts_found == ["us-gaap:CostOfGoodsAndServicesSold"]
        assert bd.facts_missing == []

    def test_period_fact_gate_fails_when_period_reported_missing(self) -> None:
        q = self._make_question(
            expected_status="ok",
            required_periods=["2022-12-31", "2023-12-31"],
            expected_facts=["us-gaap:Revenues"],
        )
        answer = self._make_answer(
            text="revenue answer",
            retrieved_concepts=["us-gaap:Revenues"],
            fact_concepts_by_period={
                "2022-12-31": ["us-gaap:Revenues"],
                "2023-12-31": ["us-gaap:Revenues"],
            },
            missing_periods=["2023-12-31"],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.period_facts_ok is False
        assert bd.periods_missing_facts == ["2023-12-31"]

    def test_period_fact_gate_fails_when_period_lacks_expected_fact(self) -> None:
        q = self._make_question(
            expected_status="ok",
            required_periods=["2022-12-31", "2023-12-31"],
            expected_facts=["us-gaap:Revenues"],
        )
        answer = self._make_answer(
            text="revenue answer",
            retrieved_concepts=["us-gaap:Revenues"],
            fact_concepts_by_period={
                "2022-12-31": ["us-gaap:Revenues"],
                "2023-12-31": ["us-gaap:GrossProfit"],
            },
            missing_periods=[],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.period_facts_ok is False
        assert bd.facts_missing_by_period["2023-12-31"] == ["us-gaap:Revenues"]

    def test_period_fact_gate_accepts_equivalent_concepts(self) -> None:
        q = self._make_question(
            expected_status="ok",
            required_periods=["2022-12-31", "2023-12-31"],
            expected_facts=["us-gaap:CostOfGoodsAndServicesSold"],
        )
        answer = self._make_answer(
            text="cost answer",
            retrieved_concepts=["us-gaap:CostOfRevenue"],
            fact_concepts_by_period={
                "2022-12-31": ["us-gaap:CostOfRevenue"],
                "2023-12-31": ["us-gaap:CostOfRevenue"],
            },
            missing_periods=[],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.period_facts_ok is True

    def test_facts_only_without_expected_status(self) -> None:
        """expected_facts alone should trigger structured judge."""
        q = self._make_question(
            expected_facts=["us-gaap:Revenues"],
        )
        assert q.has_structured_assertions is True
        answer = self._make_answer(
            text="answer",
            retrieved_concepts=["us-gaap:Revenues"],
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd is not None

    def test_narrative_terms_must_appear_in_answer_text(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_narrative_terms=["supply chain"],
        )
        answer = self._make_answer(text="Tesla cited supply chain risks in its filing.")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd is not None
        assert bd.narrative_terms_found == ["supply chain"]
        assert bd.narrative_terms_missing == []

    def test_missing_narrative_terms_fail_structured_judge(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_narrative_terms=["supply chain"],
        )
        answer = self._make_answer(text="Automotive costs increased year over year.")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd is not None
        assert bd.narrative_terms_missing == ["supply chain"]

    # --- Calc assertion: lookup ---

    def test_calc_lookup_passes_within_tolerance(self) -> None:
        """BQ-008 style: lookup 96773000000, answer contains exact match."""
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "lookup",
                "expected_value": 96773000000,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(
            text="Tesla's total revenue in FY2023 was 96,773,000,000.00.",
            retrieved_concepts=["us-gaap:Revenues"],
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.calc_correct is True
        assert "PASS" in bd.calc_detail

    def test_calc_lookup_fails_outside_tolerance(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "lookup",
                "expected_value": 96773000000,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(
            text="Tesla revenue was 50,000,000,000.",
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.calc_correct is False
        assert "FAIL" in bd.calc_detail

    def test_calc_lookup_no_numbers_fails(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "lookup",
                "expected_value": 96773000000,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(text="Revenue was significant.", calc_intent="lookup")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.calc_correct is False
        assert "No numeric values" in bd.calc_detail

    # --- Calc assertion: pct_change ---

    def test_calc_pct_change_passes(self) -> None:
        """BQ-001 style: pct_change expected 18.80%."""
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "pct_change",
                "expected_value": 18.80,
                "tolerance": 0.02,
            },
        )
        answer = self._make_answer(
            text="Year-over-year growth rate was 18.80%.",
            calc_intent="pct_change",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.calc_correct is True

    # --- Calc assertion: ratio ---

    def test_calc_ratio_passes(self) -> None:
        """BQ-002 style: ratio GrossProfit/Revenues expected 18.25%."""
        q = self._make_question(
            expected_status="ok",
            expected_facts=["us-gaap:GrossProfit", "us-gaap:Revenues"],
            expected_calc={
                "operation": "ratio",
                "numerator": "us-gaap:GrossProfit",
                "denominator": "us-gaap:Revenues",
                "expected_value": 18.25,
                "tolerance": 0.02,
            },
        )
        answer = self._make_answer(
            text="Gross profit margin for FY2023 was 18.25%.",
            retrieved_concepts=["us-gaap:GrossProfit", "us-gaap:Revenues"],
            calc_intent="ratio",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.calc_correct is True
        assert bd.facts_missing == []

    # --- Calc assertion: rank (no expected_value) ---

    def test_calc_rank_operation_only_passes(self) -> None:
        """Rank operation with no expected_value — type check only."""
        q = self._make_question(
            expected_status="ok",
            expected_calc={"operation": "rank"},
        )
        answer = self._make_answer(text="Q2 2023 had the highest margin.", calc_intent="rank")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.calc_correct is True
        assert "Operation type assertion only" in bd.calc_detail

    def test_operation_mismatch_fails_structured_judge(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={"operation": "ratio"},
        )
        answer = self._make_answer(
            text="The value is 18.25%.",
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.operation_ok is False
        assert "FAIL" in bd.operation_detail

    def test_missing_operation_signal_fails_structured_judge(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={"operation": "lookup", "expected_value": 100, "tolerance": 0.01},
        )
        answer = self._make_answer(text="Value is 100.")
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.operation_ok is False
        assert "Missing calculation_intent" in bd.operation_detail

    def test_lookup_allows_step_trace_operation(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={"operation": "lookup", "expected_value": 100, "tolerance": 0.01},
        )
        answer = self._make_answer(
            text="Value is 100.",
            calc_intent="step_trace",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.operation_ok is True

    # --- Calc value found in calculation_trace ---

    def test_calc_value_found_in_trace(self) -> None:
        """Numeric value may appear in calculation_trace, not answer text."""
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "lookup",
                "expected_value": 96773000000,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(
            text="Revenue result for FY2023:",
            calc_trace=["us-gaap:Revenues (2023-12-31) = 96,773,000,000.00"],
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is True
        assert bd.calc_correct is True

    # --- Combined: status fail overrides calc pass ---

    def test_status_fail_overrides_calc_pass(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_calc={
                "operation": "lookup",
                "expected_value": 100,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(
            status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            text="Value is 100.",
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.status_ok is False
        assert bd.calc_correct is True  # Calc itself passed

    # --- Combined: facts fail overrides calc pass ---

    def test_facts_fail_overrides_calc_pass(self) -> None:
        q = self._make_question(
            expected_status="ok",
            expected_facts=["us-gaap:Revenues"],
            expected_calc={
                "operation": "lookup",
                "expected_value": 100,
                "tolerance": 0.01,
            },
        )
        answer = self._make_answer(
            text="Value is 100.",
            retrieved_concepts=[],  # Facts not found
            calc_intent="lookup",
        )
        passed, bd = EvaluationRunner._structured_check(q, answer)
        assert passed is False
        assert bd.facts_missing == ["us-gaap:Revenues"]
        assert bd.calc_correct is True

    # --- Number extraction edge cases ---

    def test_extract_numbers_handles_commas(self) -> None:
        nums = EvaluationRunner._extract_numbers("Revenue: 96,773,000,000.00 USD")
        assert 96773000000.0 in nums

    def test_extract_numbers_handles_negative(self) -> None:
        nums = EvaluationRunner._extract_numbers("Change: -5.23%")
        assert -5.23 in nums

    def test_extract_numbers_multiple(self) -> None:
        nums = EvaluationRunner._extract_numbers("From 81,462 to 96,773 (18.80%)")
        assert len(nums) == 3
        assert 81462.0 in nums
        assert 96773.0 in nums
        assert 18.80 in nums

    # --- CalcOperation enum validation ---

    def test_invalid_operation_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExpectedCalc(operation="bogus_op", expected_value=100)

    def test_valid_operations_accepted(self) -> None:
        for op in CalcOperation:
            calc = ExpectedCalc(operation=op.value, expected_value=1.0)
            assert calc.operation == op


# ---------------------------------------------------------------------------
# Dual-track runner integration
# ---------------------------------------------------------------------------


class TestDualTrackRunner:
    """Verify _run_single populates dual-track fields correctly."""

    def _make_pipeline(
        self,
        status: AnswerStatus = AnswerStatus.OK,
        text: str = "Tesla revenue in 2023 was 96,773,000,000.00.",
        retrieved_concepts: list[str] | None = None,
        calc_intent: str | None = "lookup",
    ):
        def pipeline(question: str) -> AnswerPayload:
            debug = {}
            if retrieved_concepts is not None:
                debug["retrieved_fact_concepts"] = retrieved_concepts
            if calc_intent is not None:
                debug["calculation_intent"] = calc_intent
            return AnswerPayload(
                plan_id=uuid4(),
                status=status,
                answer_text=text,
                retrieval_debug=debug,
            )

        return pipeline

    def test_dual_track_both_pass(self) -> None:
        """Question with both legacy and structured assertions — both pass."""
        questions = [
            {
                "question_id": "DT-001",
                "question": "Revenue?",
                "category": "cross_year",
                "difficulty": "easy",
                "expected_answer_contains": ["96,773,000,000.00"],
                "expected_status": "ok",
                "expected_calc": {
                    "operation": "lookup",
                    "expected_value": 96773000000,
                    "tolerance": 0.01,
                },
            },
        ]
        bf = Path("/tmp/dt_test_bq.json")
        bf.write_text(json.dumps(questions))

        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=bf,
        )
        run = runner.run_all()
        r = run.results[0]
        assert r.passed is True
        assert r.legacy_passed is True
        assert r.structured_passed is True
        assert r.judge_breakdown is not None
        assert r.judge_breakdown.status_ok is True

    def test_structured_answer_still_requires_explicit_keywords(self) -> None:
        """Structured retrieval coverage must not hide an off-topic final answer."""
        questions = [
            {
                "question_id": "DT-002",
                "question": "Revenue?",
                "category": "cross_year",
                "difficulty": "easy",
                # Legacy keywords that won't match
                "expected_answer_contains": ["96,773,000,000.00", "result"],
                # Structured assertions that should pass
                "expected_status": "ok",
                "expected_calc": {
                    "operation": "lookup",
                    "expected_value": 96773000000,
                    "tolerance": 0.01,
                },
            },
        ]
        bf = Path("/tmp/dt_test_bq2.json")
        bf.write_text(json.dumps(questions))

        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=bf,
        )
        run = runner.run_all()
        r = run.results[0]
        assert r.passed is False
        assert r.legacy_passed is False
        assert r.structured_passed is True

    def test_no_structured_falls_back_to_legacy(self) -> None:
        """Old-style question with no structured assertions → legacy only."""
        questions = [
            {
                "question_id": "DT-003",
                "question": "Revenue?",
                "category": "cross_year",
                "difficulty": "easy",
                "expected_answer_contains": ["revenue", "2023"],
            },
        ]
        bf = Path("/tmp/dt_test_bq3.json")
        bf.write_text(json.dumps(questions))

        runner = EvaluationRunner(
            pipeline=self._make_pipeline(),
            benchmark_path=bf,
        )
        run = runner.run_all()
        r = run.results[0]
        assert r.passed is True
        assert r.legacy_passed is True
        assert r.structured_passed is None
        assert r.judge_breakdown is None


# ---------------------------------------------------------------------------
# Run summary model tests
# ---------------------------------------------------------------------------


class TestRunSummary:
    def test_pass_rate_bounds(self) -> None:
        summary = RunSummary(
            total=10,
            pass_count=7,
            fail_count=2,
            error_count=1,
            avg_latency_ms=50.0,
            pass_rate=0.7,
        )
        assert 0.0 <= summary.pass_rate <= 1.0
        assert summary.pass_rate == 0.7

    def test_zero_total(self) -> None:
        summary = RunSummary(
            total=0,
            pass_count=0,
            fail_count=0,
            error_count=0,
            avg_latency_ms=0.0,
            pass_rate=0.0,
        )
        assert summary.total == 0

    def test_pass_rate_is_recomputed_from_counts(self) -> None:
        summary = RunSummary(
            total=4,
            pass_count=1,
            fail_count=2,
            error_count=1,
            avg_latency_ms=12.0,
            pass_rate=0.99,
        )

        assert summary.pass_rate == 0.25


# ---------------------------------------------------------------------------
# Demo response contract tests (UI wiring smoke check)
# ---------------------------------------------------------------------------


class TestDemoResponseContract:
    """Verify the demo pipeline returns objects matching the UI contract."""

    def test_workbench_scope_filters_results(self) -> None:
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.info.provider_name = "mock"
        mock_provider.info.as_dict.return_value = {}
        mock_provider.embed_texts.side_effect = lambda texts: [[0.0]] * len(texts)
        mock_provider.generate_grounded_answer.return_value = "Mock answer"

        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider=mock_provider,
        )
        question = "What was Tesla's total revenue in FY2023?"

        _, _, answer_2023 = pipeline.run(
            question,
            scope=FilingScope(fiscal_years=(2023,)),
        )
        _, _, answer_2022 = pipeline.run(
            question,
            scope=FilingScope(fiscal_years=(2022,)),
        )

        assert answer_2023.status == AnswerStatus.OK
        assert answer_2022.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer_2023.retrieval_debug["active_scope"]["fiscal_years"] == [2023]

    def test_composite_provider_answer_falls_back_when_remote_drops_narrative(self) -> None:
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.info.provider_name = "mock"
        mock_provider.info.as_dict.return_value = {}
        mock_provider.embed_texts.side_effect = lambda texts: [[0.0]] * len(texts)
        mock_provider.generate_grounded_answer.return_value = (
            "The cost of automotive sales revenue increased by $15.52 billion."
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider=mock_provider,
        )
        question = (
            "What supply chain risk factors did Tesla mention in its 2023 10-K, "
            "and how did cost of automotive revenue change between FY2022 and FY2023?"
        )

        _, _, answer = pipeline.run(question)

        assert answer.status == AnswerStatus.OK
        assert "supply chain" in answer.answer_text.lower()
        assert answer.retrieval_debug["composite_local_fallback_used"] is True

    def test_composite_provider_answer_falls_back_for_chinese_cues(self) -> None:
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.info.provider_name = "mock"
        mock_provider.info.as_dict.return_value = {}
        mock_provider.embed_texts.side_effect = lambda texts: [[0.0]] * len(texts)
        mock_provider.generate_grounded_answer.return_value = (
            "Based on the provided financial data, the company's free cash flow for FY2023 "
            "is $4.36 billion."
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider=mock_provider,
        )
        question = "2023年10-K中，特斯拉提到了哪些供应链风险？FY2022到FY2023汽车销售成本如何变化？"

        _, _, answer = pipeline.run(question)

        assert answer.status == AnswerStatus.OK
        assert "供应链" in answer.answer_text
        assert answer.retrieval_debug["composite_local_fallback_used"] is True

    def test_chinese_query_defaults_remote_response_language(self) -> None:
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.info.provider_name = "mock"
        mock_provider.info.as_dict.return_value = {}
        mock_provider.embed_texts.side_effect = lambda texts: [[0.0]] * len(texts)
        mock_provider.generate_grounded_answer.return_value = "模拟答案"

        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider=mock_provider,
        )

        pipeline.run("特斯拉FY2023的毛利率是多少？请展示毛利润除以总营收的计算过程。")

        kwargs = mock_provider.generate_grounded_answer.call_args.kwargs
        assert kwargs["response_language"] == response_language_directive("zh_CN")

    def test_answer_payload_has_required_fields(self) -> None:
        plan_id = uuid4()
        doc_id = uuid4()
        answer = AnswerPayload(
            plan_id=plan_id,
            status=AnswerStatus.OK,
            answer_text="Test answer.",
            citations=[
                Citation(
                    chunk_id=uuid4(),
                    doc_id=doc_id,
                    filing_type=FilingType.ANNUAL,
                    period_end=date(2023, 12, 31),
                    excerpt="Excerpt",
                )
            ],
            calculation_trace=["Step 1: a = 1", "Step 2: b = 2"],
            confidence=0.9,
        )
        assert answer.status == AnswerStatus.OK
        assert len(answer.citations) == 1
        assert len(answer.calculation_trace) == 2
        assert answer.confidence is not None

    def test_evidence_bundle_has_required_fields(self) -> None:
        plan_id = uuid4()
        doc_id = uuid4()
        bundle = EvidenceBundle(
            plan_id=plan_id,
            section_chunks=[
                SectionChunk(
                    doc_id=doc_id,
                    kind=ChunkKind.SECTION,
                    section_title="Test",
                    text="Content",
                    token_count=1,
                )
            ],
            table_chunks=[
                TableChunk(
                    doc_id=doc_id,
                    kind=ChunkKind.TABLE,
                    section_title="Test",
                    caption="Cap",
                    headers=["A", "B"],
                    rows=[["1", "2"]],
                    raw_text="raw",
                )
            ],
            facts=[
                FactRecord(
                    doc_id=doc_id,
                    concept="us-gaap:Revenues",
                    label="Revenue",
                    value=1000.0,
                    unit="USD",
                    period_end=date(2023, 12, 31),
                )
            ],
            retrieval_scores={"abc": 0.95},
            metadata={"search_mode": "hybrid"},
        )
        assert len(bundle.section_chunks) == 1
        assert len(bundle.table_chunks) == 1
        assert len(bundle.facts) == 1
        assert "abc" in bundle.retrieval_scores

    def test_query_plan_from_planner(self) -> None:
        from tesla_finrag.planning import RuleBasedQueryPlanner

        planner = RuleBasedQueryPlanner()
        plan = planner.plan("What was Tesla's total revenue in FY2023 compared to FY2022?")
        assert plan.query_type in list(QueryType)
        assert len(plan.required_periods) >= 1
        assert len(plan.required_concepts) >= 1
        assert "us-gaap:Revenues" in plan.required_concepts


class TestWorkbenchHelpers:
    def test_filing_scope_matches_selected_quarters_only(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(corpus_repo=corpus_repo, facts_repo=facts_repo)
        quarterly_filing = next(
            filing
            for filing in pipeline._corpus_repo.list_filings()
            if filing.fiscal_year == 2023 and filing.fiscal_quarter == 2
        )
        annual_filing = next(
            filing
            for filing in pipeline._corpus_repo.list_filings()
            if filing.filing_type == FilingType.ANNUAL
        )

        scope = FilingScope(
            fiscal_years=(2023,),
            filing_type=FilingType.QUARTERLY,
            quarters=(2,),
        )

        assert scope.matches(quarterly_filing) is True
        assert scope.matches(annual_filing) is False

    def test_make_filing_rolls_december_to_next_year(self) -> None:
        from tesla_finrag.evaluation.workbench import _make_filing

        filing = _make_filing(
            FilingType.ANNUAL,
            date(2023, 12, 31),
            2023,
            None,
            "data/raw/Tesla_2023_全年_10-K.pdf",
        )

        assert filing.filed_at == date(2024, 1, 15)

    def test_question_result_accepts_error_status_enum(self) -> None:
        from tesla_finrag.evaluation.models import QuestionResult

        result = QuestionResult(
            question_id="ERR-001",
            answer_status=ResultStatus.ERROR,
            answer_text="",
            latency_ms=0.0,
            passed=False,
        )

        assert result.answer_status == ResultStatus.ERROR


# ---------------------------------------------------------------------------
# Project-level benchmark data integrity
# ---------------------------------------------------------------------------


class TestBenchmarkDataIntegrity:
    """Verify the actual project benchmark and failure analysis files."""

    _PROJECT_ROOT = Path(__file__).resolve().parents[1]

    def test_benchmark_questions_file_loads(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        assert len(questions) >= 5, "Spec requires at least 5 benchmark questions"

    def test_failure_analyses_file_loads(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
        if not path.exists():
            pytest.skip("failure_analyses.json not found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        analyses = [FailureAnalysis.model_validate(item) for item in raw]
        assert len(analyses) >= 5, "Spec requires at least 5 failure analyses"

    def test_benchmark_question_ids_unique(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        ids = [q.question_id for q in questions]
        assert len(ids) == len(set(ids)), "Question IDs must be unique"

    def test_benchmark_question_count_matches_recruiting_scope(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        assert 12 <= len(questions) <= 20, "Recruiting benchmark should stay between 12 and 20"

    def test_each_question_category_has_multiple_examples(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        category_counts = {category: 0 for category in QuestionCategory}
        for question in questions:
            category_counts[question.category] += 1
        assert all(count >= 2 for count in category_counts.values()), (
            "Each benchmark category should have at least two questions"
        )

    def test_benchmark_includes_chinese_questions(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        chinese_count = sum(
            1
            for question in questions
            if any("\u4e00" <= ch <= "\u9fff" for ch in question.question)
        )
        assert chinese_count >= 5, "Benchmark should include a meaningful set of Chinese questions"

    def test_benchmark_includes_niche_financial_questions(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
        if not path.exists():
            pytest.skip("benchmark_questions.json not found")
        questions = load_benchmark_questions(path)
        ids = {question.question_id for question in questions}
        assert {"BQ-016", "BQ-017", "BQ-018", "BQ-019"} <= ids

    def test_failure_analysis_case_ids_unique(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
        if not path.exists():
            pytest.skip("failure_analyses.json not found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        analyses = [FailureAnalysis.model_validate(item) for item in raw]
        ids = [fa.case_id for fa in analyses]
        assert len(ids) == len(set(ids)), "Case IDs must be unique"


# ---------------------------------------------------------------------------
# Baseline summary model and round-trip tests
# ---------------------------------------------------------------------------


class TestBaselineSummary:
    """Verify BaselineSummary model and save/load round-trip."""

    def test_parse_baseline_summary(self) -> None:
        summary = RunSummary(
            total=9,
            pass_count=3,
            fail_count=6,
            error_count=0,
            avg_latency_ms=0.34,
            pass_rate=0.3333,
        )
        baseline = BaselineSummary(
            run_id="abc123",
            timestamp="2026-03-07T17:36:48Z",
            run_file="data/evaluation/runs/run_20260307_173648_abc123.json",
            summary=summary,
            question_pass_fail={"BQ-001": True, "BQ-002": False},
        )
        assert baseline.run_id == "abc123"
        assert baseline.summary.pass_count == 3
        assert baseline.question_pass_fail["BQ-001"] is True
        assert baseline.question_pass_fail["BQ-002"] is False

    def test_save_and_load_baseline_round_trip(self, tmp_path: Path) -> None:
        from uuid import uuid4 as _uuid4

        def pipeline(q: str) -> AnswerPayload:
            return AnswerPayload(
                plan_id=_uuid4(),
                status=AnswerStatus.OK,
                answer_text="Tesla revenue in 2023 was $96.77B. Gross margin was 18.2%.",
                confidence=0.85,
            )

        benchmark_data = [
            {
                "question_id": "RT-001",
                "question": "What was revenue?",
                "category": "cross_year",
                "difficulty": "easy",
                "expected_answer_contains": ["revenue", "2023"],
                "required_periods": ["2023-12-31"],
                "required_concepts": ["us-gaap:Revenues"],
            },
        ]
        bf = tmp_path / "bq.json"
        bf.write_text(json.dumps(benchmark_data), encoding="utf-8")

        runner = EvaluationRunner(pipeline=pipeline, benchmark_path=bf)
        run = runner.run_all()
        run_file = runner.save_run(run, output_dir=tmp_path / "runs")
        baseline_path = runner.save_baseline(
            run, run_file, baseline_path=tmp_path / "latest_baseline.json"
        )

        assert baseline_path.exists()
        loaded = load_baseline(baseline_path)
        assert loaded.run_id == run.run_id
        assert loaded.summary.total == 1
        assert loaded.summary.pass_count == 1
        assert loaded.question_pass_fail["RT-001"] is True

    def test_load_baseline_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path / "nonexistent.json")


class TestEvaluationRunnerCliAcceptance:
    def _sample_run(self) -> EvaluationRun:
        return EvaluationRun(
            total_questions=1,
            results=[
                {
                    "question_id": "CL-001",
                    "answer_status": "ok",
                    "answer_text": "ok",
                    "latency_ms": 1.0,
                    "passed": True,
                }
            ],
            summary=RunSummary(
                total=1,
                pass_count=1,
                fail_count=0,
                error_count=0,
                avg_latency_ms=1.0,
                pass_rate=1.0,
            ),
        )

    def test_main_ignores_process_argv_without_explicit_args(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        class DummyRunner:
            def __init__(self) -> None:
                self.baseline_calls = 0

            def run_all(self) -> EvaluationRun:
                return TestEvaluationRunnerCliAcceptance()._sample_run()

            def save_run(self, run: EvaluationRun) -> Path:
                path = tmp_path / "run.json"
                path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
                return path

            def save_baseline(self, run: EvaluationRun, run_file: Path) -> Path:
                self.baseline_calls += 1
                return tmp_path / "latest_baseline.json"

        dummy_runner = DummyRunner()
        monkeypatch.setattr(runner_module, "EvaluationRunner", lambda: dummy_runner)
        monkeypatch.setattr(
            runner_module.sys,
            "argv",
            ["pytest", "-q", "tests/test_evaluation.py", "tests/test_bootstrap.py"],
        )

        runner_module.main()

        assert dummy_runner.baseline_calls == 0
        assert "Latest accepted baseline unchanged" in capsys.readouterr().out

    def test_main_updates_baseline_with_explicit_accept_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        class DummyRunner:
            def __init__(self) -> None:
                self.baseline_calls = 0

            def run_all(self) -> EvaluationRun:
                return TestEvaluationRunnerCliAcceptance()._sample_run()

            def save_run(self, run: EvaluationRun) -> Path:
                path = tmp_path / "run.json"
                path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
                return path

            def save_baseline(self, run: EvaluationRun, run_file: Path) -> Path:
                self.baseline_calls += 1
                path = tmp_path / "latest_baseline.json"
                path.write_text("{}", encoding="utf-8")
                return path

        dummy_runner = DummyRunner()
        monkeypatch.setattr(runner_module, "EvaluationRunner", lambda: dummy_runner)
        monkeypatch.setattr(
            runner_module.sys,
            "argv",
            ["pytest", "-q", "tests/test_evaluation.py", "tests/test_bootstrap.py"],
        )

        runner_module.main(["--accept-baseline"])

        assert dummy_runner.baseline_calls == 1
        assert "Latest accepted baseline updated" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Baseline discoverability and artifact integrity
# ---------------------------------------------------------------------------


class TestBaselineDiscoverability:
    """Verify that the project-level baseline pointer and delivery artifacts exist."""

    _PROJECT_ROOT = Path(__file__).resolve().parents[1]

    def test_latest_baseline_file_exists_and_loads(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "latest_baseline.json"
        if not path.exists():
            pytest.skip("latest_baseline.json not found")
        baseline = load_baseline(path)
        assert baseline.run_id, "Baseline must reference a run_id"
        assert baseline.summary.total > 0, "Baseline must have at least one question"

    def test_baseline_run_file_reference_exists(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "latest_baseline.json"
        if not path.exists():
            pytest.skip("latest_baseline.json not found")
        baseline = load_baseline(path)
        run_file = self._PROJECT_ROOT / baseline.run_file
        assert run_file.exists(), (
            f"Baseline references run file {baseline.run_file} which does not exist"
        )

    def test_failure_analyses_reference_latest_baseline_failures(self) -> None:
        baseline_path = self._PROJECT_ROOT / "data" / "evaluation" / "latest_baseline.json"
        fa_path = self._PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
        if not baseline_path.exists() or not fa_path.exists():
            pytest.skip("latest_baseline.json or failure_analyses.json not found")

        baseline = load_baseline(baseline_path)
        analyses = load_failure_analyses(fa_path)
        failed_question_ids = {
            question_id for question_id, passed in baseline.question_pass_fail.items() if not passed
        }

        for fa in analyses:
            assert fa.question_id in baseline.question_pass_fail, (
                f"Failure analysis {fa.case_id} references unknown question {fa.question_id}"
            )
            if fa.status == "resolved" and fa.resolved_run_id == baseline.run_id:
                assert baseline.question_pass_fail[fa.question_id] is True, (
                    f"Failure analysis {fa.case_id} is marked resolved "
                    "but fails in current baseline."
                )
            else:
                assert fa.baseline_run_id == baseline.run_id, (
                    f"Failure analysis {fa.case_id} must reference baseline {baseline.run_id}"
                )
                assert baseline.question_pass_fail[fa.question_id] is False, (
                    f"Failure analysis {fa.case_id} must reference a failed baseline question"
                )

        unresolved_question_ids = {fa.question_id for fa in analyses if fa.status != "resolved"}
        assert unresolved_question_ids == failed_question_ids, (
            "Failure analyses must cover every failed question in the latest baseline"
        )

    def test_delivery_report_exists(self) -> None:
        path = self._PROJECT_ROOT / "docs" / "DELIVERY.md"
        assert path.exists(), "Delivery report docs/DELIVERY.md must exist"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 100, "Delivery report must have substantive content"

    def test_delivery_report_corpus_counts_match_raw_data(self) -> None:
        path = self._PROJECT_ROOT / "docs" / "DELIVERY.md"
        if not path.exists():
            pytest.skip("docs/DELIVERY.md not found")
        content = path.read_text(encoding="utf-8")

        annual_count = len(list((self._PROJECT_ROOT / "data" / "raw").glob("*_全年_10-K.pdf")))
        quarterly_count = len(list((self._PROJECT_ROOT / "data" / "raw").glob("*_Q*_10-Q.pdf")))
        xbrl_count = (
            1 if (self._PROJECT_ROOT / "data" / "raw" / "companyfacts.json").exists() else 0
        )
        total = annual_count + quarterly_count + xbrl_count
        quarterly_line = (
            "| 10-Q (Quarterly) | 2021 Q1–Q3, 2022 Q1–Q3, 2023 Q1–Q3, "
            f"2024 Q1–Q3, 2025 Q1–Q3 | {quarterly_count} |"
        )

        assert f"| 10-K (Annual) | 2021–2025 | {annual_count} |" in content
        assert quarterly_line in content
        assert f"| **Total source files** | | **{total}** |" in content
