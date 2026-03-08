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

from tesla_finrag.evaluation.models import (
    BenchmarkQuestion,
    Difficulty,
    EvaluationRun,
    FailureAnalysis,
    QuestionCategory,
    ResultStatus,
    RunSummary,
    Severity,
)
from tesla_finrag.evaluation.runner import (
    EvaluationRunner,
    load_benchmark_questions,
    load_failure_analyses,
)
from tesla_finrag.evaluation.workbench import (
    FilingScope,
    ProviderMode,
    WorkbenchPipeline,
    _seed_demo_repositories,
)
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

    def test_load_failure_analyses_helper(self, failure_analyses_file: Path) -> None:
        analyses = load_failure_analyses(failure_analyses_file)
        assert len(analyses) == 1
        assert analyses[0].question_id == "TEST-001"

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
        assert EvaluationRunner._check_answer(question, answer) is False


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

    def test_failure_analysis_case_ids_unique(self) -> None:
        path = self._PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
        if not path.exists():
            pytest.skip("failure_analyses.json not found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        analyses = [FailureAnalysis.model_validate(item) for item in raw]
        ids = [fa.case_id for fa in analyses]
        assert len(ids) == len(set(ids)), "Case IDs must be unique"
