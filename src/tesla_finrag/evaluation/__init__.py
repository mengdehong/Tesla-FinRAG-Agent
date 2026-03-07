"""Evaluation framework: benchmark questions, failure analysis, and regression runner."""

from tesla_finrag.evaluation.models import (
    BenchmarkQuestion,
    EvaluationRun,
    FailureAnalysis,
    QuestionResult,
    RunSummary,
)
from tesla_finrag.evaluation.runner import EvaluationRunner, load_failure_analyses
from tesla_finrag.evaluation.workbench import FilingScope, WorkbenchPipeline, get_workbench_pipeline

__all__ = [
    "BenchmarkQuestion",
    "EvaluationRun",
    "EvaluationRunner",
    "FailureAnalysis",
    "FilingScope",
    "QuestionResult",
    "RunSummary",
    "WorkbenchPipeline",
    "get_workbench_pipeline",
    "load_failure_analyses",
]
