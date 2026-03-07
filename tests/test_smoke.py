"""Runtime and CLI smoke tests for the workbench pipeline.

Covers:
- Local-mode success (deterministic pipeline, no network calls).
- Remote-mode explicit failure when credentials are missing.
- CLI ``ask`` subcommand integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tesla_finrag.evaluation.workbench import (
    ProviderMode,
    WorkbenchPipeline,
    _seed_demo_repositories,
)
from tesla_finrag.provider import ProviderError

# ---------------------------------------------------------------------------
# Runtime: local mode
# ---------------------------------------------------------------------------


class TestLocalModeRuntime:
    """Verify that local mode works end-to-end without any provider."""

    def test_local_mode_answers_question(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.LOCAL,
        )

        plan, bundle, answer = pipeline.run("What was Tesla's 2023 revenue?")

        assert answer.status.value == "ok"
        assert answer.answer_text  # non-empty
        assert answer.retrieval_debug["provider_mode"] == "local"
        assert answer.retrieval_debug["embedding_provider"] == "none"
        assert answer.retrieval_debug["answer_provider"] == "template"
        assert answer.retrieval_debug["answer_model"] == "none"
        assert answer.retrieval_debug["vector_hits"] == 0

    def test_local_mode_is_default(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
        )
        assert pipeline.provider_mode == ProviderMode.LOCAL

    def test_local_mode_has_citations(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
        )
        answer = pipeline.answer_question("What was Tesla's 2023 revenue?")
        assert len(answer.citations) > 0


# ---------------------------------------------------------------------------
# Runtime: remote mode failure
# ---------------------------------------------------------------------------


class TestRemoteModeFailure:
    """Verify that remote mode fails explicitly without credentials."""

    def test_remote_mode_no_provider_raises(self) -> None:
        """Pipeline with openai-compatible mode but no provider instance."""
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=None,
        )
        with pytest.raises(ProviderError, match="no provider was configured"):
            pipeline.run("What was Tesla's 2023 revenue?")

    def test_from_settings_missing_key_raises(self) -> None:
        """OpenAIProvider.from_settings fails with empty API key."""
        from tesla_finrag.provider import OpenAIProvider
        from tesla_finrag.settings import AppSettings

        settings = AppSettings(
            openai_api_key="",
            _env_file=None,  # type: ignore[call-arg]
        )
        with pytest.raises(ProviderError, match="API key is required"):
            OpenAIProvider.from_settings(settings)

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_remote_embedding_failure_propagates(self, mock_openai_cls: MagicMock) -> None:
        """If the embedding call fails, ProviderError propagates."""
        import openai as openai_module

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = openai_module.OpenAIError("Connection refused")
        mock_openai_cls.return_value = mock_client

        from tesla_finrag.provider import OpenAIProvider

        provider = OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
        )
        with pytest.raises(ProviderError, match="Embedding request failed"):
            pipeline.run("What was Tesla's 2023 revenue?")


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLISmoke:
    """Smoke tests for the ``ask`` CLI subcommand."""

    def test_cli_local_text_output(self) -> None:
        question = (
            "Compare Tesla's total revenue between FY2022 and FY2023. "
            "What was the year-over-year growth rate?"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tesla_finrag",
                "ask",
                "--question",
                question,
                "--provider",
                "local",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "Status: ok" in result.stdout
        assert "Citations:" in result.stdout
        assert "Calculation Trace:" in result.stdout

    def test_cli_local_json_output(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tesla_finrag",
                "ask",
                "--question",
                "What was Tesla's 2023 revenue?",
                "--provider",
                "local",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert "answer_text" in payload
        assert payload["retrieval_debug"]["provider_mode"] == "local"
        assert payload["retrieval_debug"]["answer_model"] == "none"

    def test_cli_remote_mode_missing_key_fails(self) -> None:
        """CLI exits with error when remote mode has no API key."""
        env_override = {
            "OPENAI_API_KEY": "",
            "PATH": subprocess.os.environ.get("PATH", ""),
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tesla_finrag",
                "ask",
                "--question",
                "Test",
                "--provider",
                "openai-compatible",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env_override,
        )
        assert result.returncode != 0
        assert "API key" in result.stderr or "Error" in result.stderr

    def test_cli_help_shows_ask(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tesla_finrag", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "ask" in result.stdout
