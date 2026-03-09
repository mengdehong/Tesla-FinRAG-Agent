"""Repeatable evaluation runner for benchmark questions.

Usage as CLI::

    uv run python -m tesla_finrag.evaluation.runner

Or programmatically::

    from tesla_finrag.evaluation import EvaluationRunner
    runner = EvaluationRunner()
    run = runner.run_all()
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from tesla_finrag.models import AnswerPayload, AnswerStatus
from tesla_finrag.runtime import ProcessedCorpusError

from .models import (
    BaselineSummary,
    BenchmarkQuestion,
    EvaluationRun,
    FailureAnalysis,
    JudgeBreakdown,
    QuestionResult,
    ResultStatus,
    RunSummary,
)
from .workbench import get_workbench_pipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BENCHMARK_PATH = _PROJECT_ROOT / "data" / "evaluation" / "benchmark_questions.json"
_FAILURE_ANALYSIS_PATH = _PROJECT_ROOT / "data" / "evaluation" / "failure_analyses.json"
_RUNS_DIR = _PROJECT_ROOT / "data" / "evaluation" / "runs"
_BASELINE_PATH = _PROJECT_ROOT / "data" / "evaluation" / "latest_baseline.json"

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


def load_baseline(path: Path | None = None) -> BaselineSummary:
    """Load the latest accepted baseline summary.

    Raises :class:`FileNotFoundError` if no baseline has been persisted yet.
    """
    p = path or _BASELINE_PATH
    if not p.exists():
        msg = f"Latest baseline not found: {p}"
        raise FileNotFoundError(msg)
    return BaselineSummary.model_validate_json(p.read_text(encoding="utf-8"))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Tesla FinRAG benchmark evaluation.")
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help=(
            "Mark this run as the latest accepted baseline by updating "
            "data/evaluation/latest_baseline.json"
        ),
    )
    return parser.parse_args([] if argv is None else argv)


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

    def save_baseline(
        self,
        run: EvaluationRun,
        run_file: Path,
        baseline_path: Path | None = None,
    ) -> Path:
        """Write a stable latest-baseline summary that points to *run_file*.

        Returns the path to the written baseline JSON.
        """
        dest = baseline_path or _BASELINE_PATH
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Build a project-root-relative path for portability.
        try:
            rel = run_file.resolve().relative_to(_PROJECT_ROOT.resolve())
        except ValueError:
            rel = run_file

        summary = BaselineSummary(
            run_id=run.run_id,
            timestamp=run.timestamp,
            run_file=str(rel),
            summary=run.summary,
            question_pass_fail={r.question_id: r.passed for r in run.results},
        )
        dest.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        return dest

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_single(self, q: BenchmarkQuestion) -> QuestionResult:
        """Run a single question through the pipeline with dual-track judging.

        Both legacy (keyword-contains) and structured (assertion-based)
        judges run independently.  The primary ``passed`` field is
        determined by ``structured_passed`` when available, falling back
        to ``legacy_passed`` when no structured assertions are configured.
        """
        try:
            start = time.monotonic()
            answer = self._pipeline(q.question)
            elapsed_ms = (time.monotonic() - start) * 1000.0

            # Legacy judge (always runs for comparison)
            legacy_passed = self._legacy_check(q, answer)

            # Structured judge (runs when question has assertions)
            structured_passed, judge_breakdown = self._structured_check(q, answer)

            # Dual-track decision: structured takes priority when available
            if structured_passed is not None:
                passed = structured_passed
            else:
                passed = legacy_passed

            return QuestionResult(
                question_id=q.question_id,
                answer_status=answer.status,
                answer_text=answer.answer_text,
                latency_ms=round(elapsed_ms, 2),
                passed=passed,
                legacy_passed=legacy_passed,
                structured_passed=structured_passed,
                judge_breakdown=judge_breakdown,
                retrieval_debug=answer.retrieval_debug,
            )
        except ProcessedCorpusError:
            # Processed-corpus bootstrap failures are startup problems, not
            # per-question benchmark outcomes. Let the CLI surface shared
            # remediation guidance and exit non-zero.
            raise
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
    def _legacy_check(q: BenchmarkQuestion, answer: AnswerPayload) -> bool:
        """Check whether the answer contains all expected key phrases (legacy judge)."""
        if answer.status != AnswerStatus.OK:
            return False
        if not q.expected_answer_contains:
            return len(answer.citations) > 0
        lower = answer.answer_text.lower()
        return all(kw.lower() in lower for kw in q.expected_answer_contains)

    # ------------------------------------------------------------------
    # Structured judge
    # ------------------------------------------------------------------

    # Pattern to extract numbers from answer text.
    # Matches: 96,773,000,000.00  |  18.80  |  -5.23  |  1234
    _NUMBER_RE = re.compile(r"-?[\d,]+\.?\d*")
    # Known concept-equivalence groups for structured fact assertions.
    # Keep this list intentionally small and explicit to avoid over-relaxing
    # benchmark correctness criteria.
    _FACT_EQUIVALENCE_GROUPS: tuple[frozenset[str], ...] = (
        frozenset(
            {
                "us-gaap:CostOfGoodsAndServicesSold",
                "us-gaap:CostOfRevenue",
            }
        ),
    )
    _OPERATION_COMPATIBILITY: dict[str, set[str]] = {
        "lookup": {"lookup", "step_trace"},
        "ratio": {"ratio"},
        "pct_change": {"pct_change"},
        "difference": {"difference"},
        "rank": {"rank"},
    }

    @staticmethod
    def _extract_numbers(text: str) -> list[float]:
        """Extract all numeric values from *text*.

        Handles comma-separated thousands (``96,773,000,000.00``) and
        negative values.  Returns an empty list when no numbers are found.
        """
        results: list[float] = []
        for match in EvaluationRunner._NUMBER_RE.finditer(text):
            raw = match.group().replace(",", "")
            try:
                results.append(float(raw))
            except ValueError:
                continue
        return results

    @staticmethod
    def _fact_concept_matches(expected_concept: str, retrieved_concepts: set[str]) -> bool:
        """Return whether *expected_concept* is satisfied by retrieved concepts.

        Supports exact match plus a small set of explicit concept-equivalence
        groups for SEC taxonomy variants.
        """
        if expected_concept in retrieved_concepts:
            return True
        for group in EvaluationRunner._FACT_EQUIVALENCE_GROUPS:
            if expected_concept in group and group.intersection(retrieved_concepts):
                return True
        return False

    @staticmethod
    def _operation_matches(expected_operation: str, actual_operation: str | None) -> bool:
        """Return whether the actual calculation intent satisfies the expected operation."""
        if actual_operation is None:
            return False
        allowed = EvaluationRunner._OPERATION_COMPATIBILITY.get(
            expected_operation,
            {expected_operation},
        )
        return actual_operation in allowed

    @staticmethod
    def _structured_check(
        q: BenchmarkQuestion,
        answer: AnswerPayload,
    ) -> tuple[bool | None, JudgeBreakdown | None]:
        """Run structured assertion judge.

        Returns ``(passed, breakdown)`` when the question has structured
        assertions, or ``(None, None)`` when no structured assertions are
        configured — signalling the runner to fall back to legacy judge.

        The judge evaluates five independent assertion axes:

        1. **Status assertion** — ``answer.status`` must equal
           ``q.expected_status`` (when set).
        2. **Facts assertion** — every concept in ``q.expected_facts``
           must appear in
           ``answer.retrieval_debug["retrieved_fact_concepts"]``.
        3. **Period facts assertion** — for questions with both
           ``expected_facts`` and ``required_periods``, each required
           period must have concept coverage in
           ``answer.retrieval_debug["fact_concepts_by_period"]`` and
           must not appear in ``answer.retrieval_debug["missing_periods"]``.
        4. **Calculation assertion** — a numeric value in the answer
           text must match ``q.expected_calc.expected_value`` within
           the configured tolerance.
        5. **Operation assertion** — ``expected_calc.operation`` must
           match ``answer.retrieval_debug["calculation_intent"]`` under
           the configured compatibility rules.

        All configured assertions must pass for the question to pass.
        """
        if not q.has_structured_assertions:
            return None, None

        # --- 1. Status assertion ---
        if q.expected_status is not None:
            status_ok = answer.status == q.expected_status
        else:
            # No explicit status requirement: accept any non-error status.
            status_ok = answer.status != AnswerStatus.CALCULATION_ERROR

        # --- 2. Facts assertion ---
        retrieved_concepts: list[str] = answer.retrieval_debug.get("retrieved_fact_concepts", [])
        retrieved_set = set(retrieved_concepts)
        facts_found: list[str] = []
        facts_missing: list[str] = []
        for concept in q.expected_facts:
            if EvaluationRunner._fact_concept_matches(concept, retrieved_set):
                facts_found.append(concept)
            else:
                facts_missing.append(concept)
        facts_ok = len(facts_missing) == 0

        # --- 3. Period facts assertion ---
        period_facts_ok: bool | None = None
        periods_missing_facts: list[str] = []
        facts_missing_by_period: dict[str, list[str]] = {}

        if q.expected_facts and q.required_periods:
            period_facts_ok = True
            missing_periods = {
                str(period) for period in answer.retrieval_debug.get("missing_periods", [])
            }
            raw_fact_concepts_by_period = answer.retrieval_debug.get("fact_concepts_by_period", {})
            fact_concepts_by_period = (
                raw_fact_concepts_by_period if isinstance(raw_fact_concepts_by_period, dict) else {}
            )

            for period in q.required_periods:
                period_key = str(period)
                period_concepts_raw = fact_concepts_by_period.get(period_key, [])
                period_concepts = (
                    {str(concept) for concept in period_concepts_raw}
                    if isinstance(period_concepts_raw, list)
                    else set()
                )
                period_missing = [
                    concept
                    for concept in q.expected_facts
                    if not EvaluationRunner._fact_concept_matches(concept, period_concepts)
                ]

                if period_key in missing_periods or period_missing:
                    period_facts_ok = False
                    periods_missing_facts.append(period_key)
                    facts_missing_by_period[period_key] = (
                        period_missing if period_missing else list(q.expected_facts)
                    )

        # --- 4. Calculation assertion ---
        calc_correct: bool | None = None
        calc_detail = ""
        operation_ok: bool | None = None
        operation_detail = ""

        if q.expected_calc is not None:
            ec = q.expected_calc
            actual_operation_raw = answer.retrieval_debug.get("calculation_intent")
            if actual_operation_raw is None:
                actual_operation = None
            elif hasattr(actual_operation_raw, "value"):
                actual_operation = str(actual_operation_raw.value).lower()
            else:
                actual_operation = str(actual_operation_raw).lower()
            expected_operation = ec.operation.value
            operation_ok = EvaluationRunner._operation_matches(
                expected_operation=expected_operation,
                actual_operation=actual_operation,
            )
            if actual_operation is None:
                operation_detail = (
                    "Missing calculation_intent in retrieval_debug while "
                    "expected_calc is configured."
                )
            else:
                operation_detail = (
                    f"Expected operation={expected_operation}, actual={actual_operation} → "
                    f"{'PASS' if operation_ok else 'FAIL'}"
                )

            if ec.expected_value is not None:
                # Extract numbers from answer text + calculation trace
                search_text = answer.answer_text
                if answer.calculation_trace:
                    search_text += " " + " ".join(answer.calculation_trace)

                candidates = EvaluationRunner._extract_numbers(search_text)

                if not candidates:
                    calc_correct = False
                    calc_detail = (
                        f"No numeric values found in answer text. Expected ≈{ec.expected_value}"
                    )
                else:
                    # Check if any extracted number matches within tolerance.
                    # Use relative tolerance: |actual - expected| / |expected| <= tol
                    expected = ec.expected_value
                    tol = ec.tolerance
                    best_match: float | None = None
                    best_rel_error = float("inf")

                    for candidate in candidates:
                        if expected == 0:
                            rel_error = abs(candidate)
                        else:
                            rel_error = abs(candidate - expected) / abs(expected)
                        if rel_error < best_rel_error:
                            best_rel_error = rel_error
                            best_match = candidate

                    calc_correct = best_rel_error <= tol
                    calc_detail = (
                        f"Expected ≈{expected}, best match = {best_match}, "
                        f"rel_error = {best_rel_error:.6f}, "
                        f"tolerance = {tol} → "
                        f"{'PASS' if calc_correct else 'FAIL'}"
                    )
            else:
                # Operation type check only — no numeric assertion.
                # The question asserts the operation type but not a specific
                # value (e.g. rank questions).
                calc_correct = True
                calc_detail = (
                    f"Operation type assertion only: {ec.operation.value}. "
                    f"No expected_value to verify."
                )

        # --- 4. Narrative cue assertion ---
        answer_lower = answer.answer_text.lower()
        narrative_terms_found: list[str] = []
        narrative_terms_missing: list[str] = []
        for term in q.expected_narrative_terms:
            if term.lower() in answer_lower:
                narrative_terms_found.append(term)
            else:
                narrative_terms_missing.append(term)
        narrative_ok = len(narrative_terms_missing) == 0

        # --- Overall pass/fail ---
        passed = status_ok and facts_ok and narrative_ok
        if period_facts_ok is not None:
            passed = passed and period_facts_ok
        if calc_correct is not None:
            passed = passed and calc_correct
        if operation_ok is not None:
            passed = passed and operation_ok

        breakdown = JudgeBreakdown(
            status_ok=status_ok,
            facts_found=facts_found,
            facts_missing=facts_missing,
            period_facts_ok=period_facts_ok,
            periods_missing_facts=periods_missing_facts,
            facts_missing_by_period=facts_missing_by_period,
            calc_correct=calc_correct,
            calc_detail=calc_detail,
            operation_ok=operation_ok,
            operation_detail=operation_detail,
            narrative_terms_found=narrative_terms_found,
            narrative_terms_missing=narrative_terms_missing,
        )

        logger.debug(
            "Structured judge for %s: status_ok=%s, facts_ok=%s, period_facts_ok=%s, "
            "operation_ok=%s, calc_correct=%s → passed=%s",
            q.question_id,
            status_ok,
            facts_ok,
            period_facts_ok,
            operation_ok,
            calc_correct,
            passed,
        )

        return passed, breakdown

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


def main(argv: list[str] | None = None) -> None:
    """Run the evaluation benchmark and print results."""
    from tesla_finrag.guidance import format_corpus_guidance
    from tesla_finrag.runtime import ProcessedCorpusError

    args = _parse_args(argv)

    try:
        runner = EvaluationRunner()
    except ProcessedCorpusError as exc:
        print(format_corpus_guidance(exc), file=sys.stderr)
        raise SystemExit(1) from None

    print("Loading benchmark questions...")
    try:
        run = runner.run_all()
    except ProcessedCorpusError as exc:
        print(format_corpus_guidance(exc), file=sys.stderr)
        raise SystemExit(1) from None

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
        # Show dual-track comparison when both judges ran
        dual = ""
        if r.legacy_passed is not None and r.structured_passed is not None:
            l_mark = "P" if r.legacy_passed else "F"
            s_mark = "P" if r.structured_passed else "F"
            dual = f" [legacy={l_mark} struct={s_mark}]"
        elif r.legacy_passed is not None:
            dual = " [legacy-only]"
        print(f"  [{mark}] {r.question_id}: {r.answer_status} ({r.latency_ms:.0f}ms){dual}")
        if r.judge_breakdown and not r.judge_breakdown.status_ok:
            print(f"         Status mismatch: got {r.answer_status}")
        if r.judge_breakdown and r.judge_breakdown.facts_missing:
            print(f"         Missing facts: {', '.join(r.judge_breakdown.facts_missing)}")
        if r.judge_breakdown and r.judge_breakdown.calc_detail:
            print(f"         Calc: {r.judge_breakdown.calc_detail}")
        if r.notes:
            print(f"         Note: {r.notes}")

    path = runner.save_run(run)
    print(f"\nRun saved to: {path}")

    if args.accept_baseline:
        baseline_path = runner.save_baseline(run, path)
        print(f"Latest accepted baseline updated: {baseline_path}")
    else:
        print(
            "Latest accepted baseline unchanged. Re-run with --accept-baseline to accept this run."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
