"""Repeatable evaluation runner for benchmark questions.

Usage as CLI::

    uv run python -m tesla_finrag.evaluation.runner

Or programmatically::

    from tesla_finrag.evaluation import EvaluationRunner
    runner = EvaluationRunner()
    run = runner.run_all()
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from tesla_finrag.models import AnswerPayload, AnswerStatus

from .models import (
    BenchmarkQuestion,
    EvaluationRun,
    FailureAnalysis,
    QuestionResult,
    ResultStatus,
    RunSummary,
)
from .workbench import get_workbench_pipeline

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BENCHMARK_PATH = _PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
_FAILURE_ANALYSIS_PATH = _PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
_RUNS_DIR = _PROJECT_ROOT / "data" / "evaluation" / "runs"

# Type alias for the pipeline callable
PipelineCallable = Callable[[str], AnswerPayload]


def _default_pipeline(question: str) -> AnswerPayload:
    """Run the shared workbench pipeline used by the local demo."""
    return get_workbench_pipeline().answer_question(question)


# ---------------------------------------------------------------------------
# Benchmark loading
# ---------------------------------------------------------------------------


def load_benchmark_questions(
    path: Path | None = None,
) -> list[BenchmarkQuestion]:
    """Load benchmark questions from the JSON file."""
    p = path or _BENCHMARK_PATH
    if not p.exists():
        msg = f"Benchmark file not found: {p}"
        raise FileNotFoundError(msg)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [BenchmarkQuestion.model_validate(item) for item in raw]


def load_failure_analyses(path: Path | None = None) -> list[FailureAnalysis]:
    """Load structured failure analyses from the JSON file."""
    p = path or _FAILURE_ANALYSIS_PATH
    if not p.exists():
        msg = f"Failure analysis file not found: {p}"
        raise FileNotFoundError(msg)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [FailureAnalysis.model_validate(item) for item in raw]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class EvaluationRunner:
    """Execute benchmark questions and produce evaluation run summaries.

    Parameters
    ----------
    pipeline:
        A callable that accepts a question string and returns an
        :class:`AnswerPayload`.  Defaults to the stub pipeline.
    benchmark_path:
        Path to the benchmark questions JSON file.
    """

    def __init__(
        self,
        pipeline: PipelineCallable | None = None,
        benchmark_path: Path | None = None,
    ) -> None:
        self._pipeline = pipeline or _default_pipeline
        self._benchmark_path = benchmark_path or _BENCHMARK_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all(self) -> EvaluationRun:
        """Load all benchmark questions and run them through the pipeline."""
        questions = load_benchmark_questions(self._benchmark_path)
        return self.run(questions)

    def run(self, questions: list[BenchmarkQuestion]) -> EvaluationRun:
        """Run a list of benchmark questions and return the evaluation run."""
        results: list[QuestionResult] = []
        for q in questions:
            result = self._run_single(q)
            results.append(result)

        summary = self._summarize(results)
        return EvaluationRun(
            total_questions=len(questions),
            results=results,
            summary=summary,
        )

    def save_run(self, run: EvaluationRun, output_dir: Path | None = None) -> Path:
        """Persist an evaluation run as a JSON file.

        Returns the path to the written file.
        """
        out = output_dir or _RUNS_DIR
        out.mkdir(parents=True, exist_ok=True)
        ts = run.timestamp.strftime("%Y%m%d_%H%M%S")
        path = out / f"run_{ts}_{run.run_id}.json"
        path.write_text(
            run.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_single(self, q: BenchmarkQuestion) -> QuestionResult:
        """Run a single question through the pipeline."""
        try:
            start = time.monotonic()
            answer = self._pipeline(q.question)
            elapsed_ms = (time.monotonic() - start) * 1000.0

            passed = self._check_answer(q, answer)
            return QuestionResult(
                question_id=q.question_id,
                answer_status=answer.status,
                answer_text=answer.answer_text,
                latency_ms=round(elapsed_ms, 2),
                passed=passed,
            )
        except Exception as exc:
            return QuestionResult(
                question_id=q.question_id,
                answer_status=ResultStatus.ERROR,
                answer_text="",
                latency_ms=0.0,
                passed=False,
                notes=str(exc),
            )

    @staticmethod
    def _check_answer(q: BenchmarkQuestion, answer: AnswerPayload) -> bool:
        """Check whether the answer contains all expected key phrases."""
        if answer.status != AnswerStatus.OK:
            return False
        if not q.expected_answer_contains:
            return len(answer.citations) > 0
        lower = answer.answer_text.lower()
        return all(kw.lower() in lower for kw in q.expected_answer_contains)

    @staticmethod
    def _summarize(results: list[QuestionResult]) -> RunSummary:
        """Produce aggregate statistics from question results."""
        total = len(results)
        pass_count = sum(1 for r in results if r.passed)
        error_count = sum(1 for r in results if r.answer_status == ResultStatus.ERROR)
        fail_count = total - pass_count - error_count
        avg_latency = sum(r.latency_ms for r in results) / total if total > 0 else 0.0
        return RunSummary(
            total=total,
            pass_count=pass_count,
            fail_count=fail_count,
            error_count=error_count,
            avg_latency_ms=round(avg_latency, 2),
            pass_rate=round(pass_count / total, 4) if total > 0 else 0.0,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the evaluation benchmark and print results."""
    runner = EvaluationRunner()

    print("Loading benchmark questions...")
    run = runner.run_all()

    print(f"\nEvaluation Run: {run.run_id}")
    print(f"Timestamp: {run.timestamp.isoformat()}")
    print(f"Questions: {run.total_questions}")
    print(
        f"Pass: {run.summary.pass_count} | Fail: {run.summary.fail_count} "
        f"| Error: {run.summary.error_count}"
    )
    print(f"Pass rate: {run.summary.pass_rate:.0%}")
    print(f"Avg latency: {run.summary.avg_latency_ms:.1f}ms")

    print("\nResults:")
    for r in run.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] {r.question_id}: {r.answer_status} ({r.latency_ms:.0f}ms)")
        if r.notes:
            print(f"         Note: {r.notes}")

    path = runner.save_run(run)
    print(f"\nRun saved to: {path}")


if __name__ == "__main__":
    main()
