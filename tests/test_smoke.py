"""Runtime and CLI smoke tests for the provider-backed workbench pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tesla_finrag.evaluation.workbench import (
    ProviderMode,
    WorkbenchPipeline,
    _seed_demo_repositories,
    get_workbench_pipeline,
)
from tesla_finrag.guidance import INGEST_COMMAND
from tesla_finrag.provider import OllamaProvider, OpenAIProvider, ProviderError
from tesla_finrag.retrieval import InMemoryRetrievalStore


def _make_fake_client(answer_text: str) -> MagicMock:
    mock_client = MagicMock()

    def fake_embed(input, model):  # noqa: A002
        response = MagicMock()
        response.data = []
        for index, _ in enumerate(input):
            item = MagicMock()
            item.index = index
            item.embedding = [float(index)] * 8
            response.data.append(item)
        return response

    mock_client.embeddings.create.side_effect = fake_embed

    mock_choice = MagicMock()
    mock_choice.message.content = answer_text
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


def _make_ollama_provider(answer_text: str = "Local Ollama answer.") -> OllamaProvider:
    return OllamaProvider(
        client=_make_fake_client(answer_text),
        embedding_model="nomic-embed-text",
        chat_model="qwen2.5:1.5b",
        base_url="http://localhost:11434/v1",
    )


def _make_remote_provider(answer_text: str = "Remote OpenAI-compatible answer.") -> OpenAIProvider:
    return OpenAIProvider(
        client=_make_fake_client(answer_text),
        embedding_model="text-embedding-3-small",
        chat_model="gpt-4o-mini",
        base_url="https://api.example.com/v1",
    )


class _FakeIndexingProvider:
    def __init__(
        self,
        *,
        embedding_model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434/v1",
        fail_message: str | None = None,
    ) -> None:
        self.embedding_model = embedding_model
        self.base_url = base_url
        self.info = SimpleNamespace(
            provider_name="shared-indexing-backend",
            embedding_model=embedding_model,
            base_url=base_url,
        )
        self._fail_message = fail_message

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._fail_message is not None:
            raise ProviderError(self._fail_message)
        return [[float(index)] * 8 for index, _ in enumerate(texts)]


class TestLocalModeRuntime:
    """Verify that public local mode is Ollama-backed."""

    def test_local_mode_answers_question(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        provider = _make_ollama_provider("Local Ollama revenue result answer.")
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.LOCAL,
            provider=provider,
            indexing_provider=_FakeIndexingProvider(),
        )

        _, _, answer = pipeline.run("What was Tesla's 2023 revenue?")

        assert answer.status.value == "ok"
        assert "Ollama" in answer.answer_text
        assert answer.retrieval_debug["provider_mode"] == "local"
        assert answer.retrieval_debug["embedding_provider"] == "shared-indexing-backend"
        assert answer.retrieval_debug["answer_provider"] == "ollama"
        assert answer.retrieval_debug["answer_model"] == "qwen2.5:1.5b"

    def test_local_mode_without_provider_raises(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.LOCAL,
            provider=None,
        )

        with pytest.raises(ProviderError, match="local Ollama provider mode selected"):
            pipeline.run("What was Tesla's 2023 revenue?")

    def test_local_mode_has_citations(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.LOCAL,
            provider=_make_ollama_provider(),
            indexing_provider=_FakeIndexingProvider(),
        )

        answer = pipeline.answer_question("What was Tesla's 2023 revenue?")
        assert len(answer.citations) > 0


class TestRemoteModeFailure:
    """Verify that remote mode still fails explicitly when misconfigured."""

    def test_remote_mode_no_provider_raises(self) -> None:
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
        from tesla_finrag.settings import AppSettings

        settings = AppSettings(
            openai_api_key="",
            _env_file=None,  # type: ignore[call-arg]
        )
        with pytest.raises(ProviderError, match="API key is required"):
            OpenAIProvider.from_settings(settings)

    def test_remote_embedding_failure_propagates(self) -> None:
        corpus_repo, facts_repo = _seed_demo_repositories()
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=_make_remote_provider(),
            retrieval_store=InMemoryRetrievalStore(),
            indexing_provider=_FakeIndexingProvider(fail_message="shared embedding failure"),
        )
        with pytest.raises(ProviderError, match="shared embedding failure"):
            pipeline.run("What was Tesla's 2023 revenue?")


class TestCLISmoke:
    """Smoke tests for the ``ask`` CLI subcommand."""

    def test_cli_local_text_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from tesla_finrag.__main__ import main
        from tesla_finrag.settings import get_settings
        from tests.test_runtime import _build_valid_fixture

        _build_valid_fixture(tmp_path)
        monkeypatch.setenv("PROCESSED_DATA_DIR", str(tmp_path))
        get_settings.cache_clear()
        get_workbench_pipeline.cache_clear()

        try:
            with (
                patch(
                    "tesla_finrag.provider.OllamaProvider.from_settings",
                    return_value=_make_ollama_provider("CLI local answer."),
                ),
                patch(
                    "tesla_finrag.provider.IndexingEmbeddingProvider.from_settings",
                    return_value=_FakeIndexingProvider(),
                ),
            ):
                result = main(
                    [
                        "ask",
                        "--question",
                        "What was Tesla's Q1 2023 revenue?",
                        "--provider",
                        "local",
                    ]
                )
            captured = capsys.readouterr()
            assert result == 0
            assert "Status: ok" in captured.out
            assert "Citations:" in captured.out
        finally:
            get_settings.cache_clear()
            get_workbench_pipeline.cache_clear()

    def test_cli_local_json_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from tesla_finrag.__main__ import main
        from tesla_finrag.settings import get_settings
        from tests.test_runtime import _build_valid_fixture

        _build_valid_fixture(tmp_path)
        monkeypatch.setenv("PROCESSED_DATA_DIR", str(tmp_path))
        get_settings.cache_clear()
        get_workbench_pipeline.cache_clear()

        try:
            with (
                patch(
                    "tesla_finrag.provider.OllamaProvider.from_settings",
                    return_value=_make_ollama_provider("CLI local JSON answer."),
                ),
                patch(
                    "tesla_finrag.provider.IndexingEmbeddingProvider.from_settings",
                    return_value=_FakeIndexingProvider(),
                ),
            ):
                result = main(
                    [
                        "ask",
                        "--question",
                        "What was Tesla's Q1 2023 revenue?",
                        "--provider",
                        "local",
                        "--json",
                    ]
                )
            captured = capsys.readouterr()
            assert result == 0
            payload = json.loads(captured.out)
            assert payload["status"] == "ok"
            assert payload["retrieval_debug"]["provider_mode"] == "local"
            assert payload["retrieval_debug"]["answer_provider"] == "ollama"
        finally:
            get_settings.cache_clear()
            get_workbench_pipeline.cache_clear()

    def test_cli_missing_processed_data_fails(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "missing-processed"
        env_override = {
            **subprocess.os.environ,
            "PROCESSED_DATA_DIR": str(missing_dir),
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
                "local",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/tmp",
            env=env_override,
        )
        assert result.returncode != 0
        assert "processed" in result.stderr.lower()

    def test_cli_remote_mode_missing_key_fails(self) -> None:
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
        if "processed" in result.stderr.lower():
            assert INGEST_COMMAND in result.stderr
        else:
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
        assert "ingest" in result.stdout


class TestSharedProcessedRuntime:
    """All surfaces must go through the same processed runtime bootstrap."""

    def test_get_workbench_pipeline_uses_processed_runtime(self, tmp_path: Path) -> None:
        from tests.test_runtime import _build_valid_fixture

        _build_valid_fixture(tmp_path)
        get_workbench_pipeline.cache_clear()
        try:
            with (
                patch(
                    "tesla_finrag.provider.OllamaProvider.from_settings",
                    return_value=_make_ollama_provider("Shared runtime answer."),
                ),
                patch(
                    "tesla_finrag.provider.IndexingEmbeddingProvider.from_settings",
                    return_value=_FakeIndexingProvider(),
                ),
            ):
                pipeline = get_workbench_pipeline(
                    provider_mode=ProviderMode.LOCAL,
                    processed_dir=str(tmp_path),
                )
            _, _, answer = pipeline.run("What was Tesla's Q1 2023 revenue?")
            assert answer.status is not None
            assert answer.retrieval_debug["provider_mode"] == "local"
            assert answer.retrieval_debug["answer_provider"] == "ollama"
        finally:
            get_workbench_pipeline.cache_clear()

    def test_evaluation_runner_default_pipeline_uses_processed_runtime(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tesla_finrag.evaluation.models import BenchmarkQuestion
        from tesla_finrag.evaluation.runner import EvaluationRunner
        from tesla_finrag.settings import get_settings
        from tests.test_runtime import _build_valid_fixture

        _build_valid_fixture(tmp_path)
        monkeypatch.setenv("PROCESSED_DATA_DIR", str(tmp_path))
        get_settings.cache_clear()
        get_workbench_pipeline.cache_clear()

        try:
            with (
                patch(
                    "tesla_finrag.provider.OllamaProvider.from_settings",
                    return_value=_make_ollama_provider("Runner local answer."),
                ),
                patch(
                    "tesla_finrag.provider.IndexingEmbeddingProvider.from_settings",
                    return_value=_FakeIndexingProvider(),
                ),
            ):
                runner = EvaluationRunner()
                questions = [
                    BenchmarkQuestion(
                        question_id="smoke-1",
                        question="What was Tesla's Q1 2023 revenue?",
                        category="cross_year",
                        difficulty="easy",
                        expected_answer_contains=[],
                    )
                ]
                run = runner.run(questions)
            assert run.total_questions == 1
            assert run.summary.error_count == 0
        finally:
            get_settings.cache_clear()
            get_workbench_pipeline.cache_clear()
