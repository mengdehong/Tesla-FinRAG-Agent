"""Tests for the runtime bootstrap workflow.

Covers:
- Ingestion CLI subcommand (task 1.1 / 1.2)
- Shared processed-corpus guidance helper (task 1.3)
- Workspace/runtime startup behaviour when processed artifacts are missing or invalid
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tesla_finrag.evaluation.models import BenchmarkQuestion
from tesla_finrag.guidance import (
    INGEST_COMMAND,
    check_corpus_readiness,
    format_corpus_guidance,
)
from tesla_finrag.logging_config import configure_cli_logging
from tesla_finrag.runtime import (
    MalformedProcessedArtifactError,
    MissingProcessedArtifactError,
    ProcessedCorpusError,
)
from tests.test_runtime import _build_valid_fixture

# ---------------------------------------------------------------------------
# 1.3  Shared processed-corpus guidance helper
# ---------------------------------------------------------------------------


class TestFormatCorpusGuidance:
    """format_corpus_guidance should produce consistent, actionable text."""

    def test_missing_artifact_includes_ingest_command(self):
        exc = MissingProcessedArtifactError("data/processed/filings not found")
        msg = format_corpus_guidance(exc)
        assert INGEST_COMMAND in msg
        assert "not ready" in msg.lower() or "not found" in msg.lower()


class TestLoggingConfig:
    """CLI logging should suppress known non-fatal pdfminer noise."""

    def test_configure_cli_logging_suppresses_known_fontbbox_warning(self, caplog):
        import logging

        configure_cli_logging()
        logger = logging.getLogger("pdfminer.pdffont")

        with caplog.at_level(logging.WARNING):
            logger.warning(
                "Could not get FontBBox from font descriptor because "
                "None cannot be parsed as 4 floats"
            )
            logger.warning("different warning")

        assert "Could not get FontBBox" not in caplog.text
        assert "different warning" in caplog.text

    def test_malformed_artifact_includes_ingest_command(self):
        exc = MalformedProcessedArtifactError("bad JSON")
        msg = format_corpus_guidance(exc)
        assert INGEST_COMMAND in msg
        assert "invalid" in msg.lower()

    def test_generic_corpus_error_includes_ingest_command(self):
        exc = ProcessedCorpusError("unknown issue")
        msg = format_corpus_guidance(exc)
        assert INGEST_COMMAND in msg


class TestCheckCorpusReadiness:
    """check_corpus_readiness returns None when ready, guidance text otherwise."""

    def test_returns_none_when_valid(self, tmp_path: Path):
        _build_valid_fixture(tmp_path)

        result = check_corpus_readiness(tmp_path)
        assert result is None

    def test_returns_guidance_when_missing(self, tmp_path: Path):
        result = check_corpus_readiness(tmp_path / "nonexistent")
        assert result is not None
        assert INGEST_COMMAND in result

    def test_returns_guidance_when_malformed(self, tmp_path: Path):
        _build_valid_fixture(tmp_path)
        facts_path = tmp_path / "facts" / "all_facts.jsonl"
        with open(facts_path, "a", encoding="utf-8") as fh:
            fh.write("NOT VALID JSON\n")

        result = check_corpus_readiness(tmp_path)

        assert result is not None
        assert "invalid" in result.lower()
        assert INGEST_COMMAND in result


# ---------------------------------------------------------------------------
# 1.1 / 1.2  Ingestion CLI subcommand
# ---------------------------------------------------------------------------


class TestIngestCLI:
    """Tests for the ``ingest`` subcommand in __main__.py."""

    def test_ingest_subcommand_is_registered(self):
        from tesla_finrag.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["ingest"])
        assert args.command == "ingest"

    def test_ingest_default_paths(self):
        from tesla_finrag.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["ingest"])
        assert args.raw_dir == "data/raw"
        assert args.output_dir == "data/processed"
        assert args.workers == 0

    def test_ingest_custom_paths(self):
        from tesla_finrag.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args([
                "ingest",
                "--raw-dir", "/tmp/raw",
                "--output-dir", "/tmp/out",
                "--workers", "3",
        ])
        assert args.raw_dir == "/tmp/raw"
        assert args.output_dir == "/tmp/out"
        assert args.workers == 3

    def test_ingest_worker_resolution_auto_caps_at_four(self, monkeypatch: pytest.MonkeyPatch):
        from tesla_finrag.__main__ import _resolve_ingest_workers

        monkeypatch.setattr("tesla_finrag.__main__.os.cpu_count", lambda: 12)
        assert _resolve_ingest_workers(0) == 4
        assert _resolve_ingest_workers(2) == 2

    def test_ingest_runs_pipeline_and_reports(self, tmp_path: Path, capsys):
        """Simulate a successful pipeline run and verify the report output."""
        from tesla_finrag.__main__ import main

        fake_summary = {
            "filings": 3,
            "section_chunks": 42,
            "table_chunks": 10,
            "fact_records": 100,
            "manifest_available": 5,
            "manifest_gaps": 1,
            "failed_filings": 0,
            "failed_details": [],
            "elapsed_seconds": 12.34,
            "gap_details": [
                {
                    "fiscal_year": 2020,
                    "fiscal_quarter": 2,
                    "filing_type": "10-Q",
                    "status": "missing",
                    "notes": "PDF not found",
                }
            ],
        }

        with mock.patch(
            "tesla_finrag.ingestion.pipeline.run_pipeline",
            return_value=fake_summary,
        ):
            rc = main(["ingest", "--raw-dir", str(tmp_path), "--output-dir", str(tmp_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "Ingestion Complete" in captured.out
        assert "Filings written:    3" in captured.out
        assert "Section chunks:     42" in captured.out
        assert "Manifest gaps:      1" in captured.out
        assert "Fact records:       100" in captured.out
        assert "FY2020" in captured.out

    def test_ingest_pipeline_failure(self, tmp_path: Path, capsys):
        """Pipeline exception should be caught and reported."""
        from tesla_finrag.__main__ import main

        with mock.patch(
            "tesla_finrag.ingestion.pipeline.run_pipeline",
            side_effect=RuntimeError("boom"),
        ):
            rc = main(["ingest", "--raw-dir", str(tmp_path), "--output-dir", str(tmp_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "boom" in captured.err

    def test_ingest_reports_failed_filings(self, tmp_path: Path, capsys):
        from tesla_finrag.__main__ import main

        fake_summary = {
            "filings": 1,
            "section_chunks": 0,
            "table_chunks": 0,
            "fact_records": 0,
            "manifest_available": 1,
            "manifest_gaps": 0,
            "failed_filings": 1,
            "failed_details": [
                {
                    "period_key": "FY2021",
                    "elapsed_seconds": 9.87,
                    "error": "broken pdf",
                }
            ],
            "elapsed_seconds": 10.0,
            "gap_details": [],
        }

        with mock.patch(
            "tesla_finrag.ingestion.pipeline.run_pipeline",
            return_value=fake_summary,
        ):
            rc = main(["ingest", "--raw-dir", str(tmp_path), "--output-dir", str(tmp_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "Ingestion Complete With Warnings" in captured.out
        assert "Failed filings:     1" in captured.out
        assert "FY2021" in captured.out


# ---------------------------------------------------------------------------
# 2.1  ask CLI uses shared guidance
# ---------------------------------------------------------------------------


class TestAskCLIGuidance:
    """The ask subcommand should display shared guidance on ProcessedCorpusError."""

    def test_ask_shows_guidance_on_missing_corpus(self, capsys):
        from tesla_finrag.__main__ import main

        with mock.patch(
            "tesla_finrag.evaluation.workbench.get_workbench_pipeline",
            side_effect=MissingProcessedArtifactError("not found"),
        ):
            rc = main(["ask", "-q", "test"])

        assert rc == 1
        captured = capsys.readouterr()
        assert INGEST_COMMAND in captured.err


class TestEvaluationRunnerGuidance:
    """The evaluation CLI should fail fast on processed-corpus startup errors."""

    def test_runner_main_shows_guidance_on_missing_corpus(self, capsys):
        from tesla_finrag.evaluation.runner import main

        questions = [
            BenchmarkQuestion(
                question_id="bootstrap-1",
                question="What was Tesla's Q1 2023 revenue?",
                category="cross_year",
                difficulty="easy",
            )
        ]

        with (
            mock.patch(
                "tesla_finrag.evaluation.runner.load_benchmark_questions",
                return_value=questions,
            ),
            mock.patch(
                "tesla_finrag.evaluation.runner.get_workbench_pipeline",
                side_effect=MissingProcessedArtifactError("not found"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert INGEST_COMMAND in captured.err
