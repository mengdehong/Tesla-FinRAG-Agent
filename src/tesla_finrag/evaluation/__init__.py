"""Evaluation framework: benchmark questions, failure analysis, and regression runner."""

from __future__ import annotations

from tesla_finrag.evaluation.models import (
    BaselineSummary,
    BenchmarkQuestion,
    EvaluationRun,
    FailureAnalysis,
    QuestionResult,
    RunSummary,
)
from tesla_finrag.evaluation.workbench import (
    FilingScope,
    ProviderMode,
    WorkbenchPipeline,
    get_workbench_pipeline,
)

__all__ = [
    "BaselineSummary",
    "BenchmarkQuestion",
    "EvaluationRun",
    "EvaluationRunner",
    "FailureAnalysis",
    "FilingScope",
    "ProviderMode",
    "QuestionResult",
    "RunSummary",
    "WorkbenchPipeline",
    "get_workbench_pipeline",
    "load_baseline",
    "load_failure_analyses",
]


def __getattr__(name: str):
    if name in {"EvaluationRunner", "load_failure_analyses", "load_baseline"}:
        from tesla_finrag.evaluation.runner import (
            EvaluationRunner,
            load_baseline,
            load_failure_analyses,
        )

        exports = {
            "EvaluationRunner": EvaluationRunner,
            "load_failure_analyses": load_failure_analyses,
            "load_baseline": load_baseline,
        }
        return exports[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
