"""Tests for local Ollama and remote OpenAI-compatible provider wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tesla_finrag.provider import (
    IndexingEmbeddingProvider,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    ProviderInfo,
)
from tesla_finrag.settings import AppSettings


def _mock_embedding_response() -> MagicMock:
    item_0 = MagicMock()
    item_0.index = 0
    item_0.embedding = [0.1, 0.2, 0.3]
    item_1 = MagicMock()
    item_1.index = 1
    item_1.embedding = [0.4, 0.5, 0.6]
    response = MagicMock()
    response.data = [item_1, item_0]
    return response


def _make_openai_provider(mock_client: MagicMock | None = None) -> OpenAIProvider:
    return OpenAIProvider(
        client=mock_client or MagicMock(),
        embedding_model="text-embedding-3-small",
        chat_model="gpt-4o-mini",
        base_url="https://api.example.com/v1",
    )


def _make_ollama_provider(mock_client: MagicMock | None = None) -> OllamaProvider:
    return OllamaProvider(
        client=mock_client or MagicMock(),
        embedding_model="nomic-embed-text",
        chat_model="qwen3.5:4b",
        base_url="http://localhost:11434/v1",
    )


class TestSettingsProviderFields:
    """Verify provider-related settings defaults and overrides."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("OLLAMA_CHAT_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_EMBEDDING_MODEL", raising=False)
        s = AppSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_base_url is None
        assert s.openai_timeout_seconds == 60
        assert s.ollama_base_url == "http://localhost:11434/v1"
        assert s.ollama_timeout_seconds == 60
        assert s.ollama_chat_model == "qwen3.5:4b"
        assert s.ollama_embedding_model == "nomic-embed-text"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "https://my-proxy.example.com/v1")
        monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "45")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:11434/v1")
        monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "90")
        monkeypatch.setenv("OLLAMA_CHAT_MODEL", "qwen3.5:4b")
        monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "mxbai-embed-large")
        s = AppSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_base_url == "https://my-proxy.example.com/v1"
        assert s.openai_timeout_seconds == 45
        assert s.ollama_base_url == "http://ollama.local:11434/v1"
        assert s.ollama_timeout_seconds == 90
        assert s.ollama_chat_model == "qwen3.5:4b"
        assert s.ollama_embedding_model == "mxbai-embed-large"

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("openai_timeout_seconds", 0),
            ("openai_timeout_seconds", 500),
            ("ollama_timeout_seconds", 0),
            ("ollama_timeout_seconds", 500),
        ],
    )
    def test_timeout_bounds(self, field_name: str, value: int) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(**{field_name: value})


class TestProviderFromSettings:
    def test_missing_api_key_raises(self) -> None:
        settings = AppSettings(openai_api_key="", _env_file=None)  # type: ignore[call-arg]
        with pytest.raises(ProviderError, match="API key is required"):
            OpenAIProvider.from_settings(settings)

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_valid_openai_settings_create_provider(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        settings = AppSettings(
            openai_api_key="sk-test-key",
            openai_base_url="https://proxy.example.com/v1",
            openai_timeout_seconds=45,
            openai_model="gpt-4o",
            embedding_model="text-embedding-3-large",
            _env_file=None,  # type: ignore[call-arg]
        )
        provider = OpenAIProvider.from_settings(settings)
        assert provider.chat_model == "gpt-4o"
        assert provider.embedding_model == "text-embedding-3-large"
        assert provider.base_url == "https://proxy.example.com/v1"
        mock_openai_cls.assert_called_once_with(
            api_key="sk-test-key",
            base_url="https://proxy.example.com/v1",
            timeout=45.0,
        )

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_default_ollama_settings_create_provider(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        settings = AppSettings(_env_file=None)  # type: ignore[call-arg]
        provider = OllamaProvider.from_settings(settings)
        assert provider.chat_model == "qwen3.5:4b"
        assert provider.embedding_model == "nomic-embed-text"
        assert provider.base_url == "http://localhost:11434/v1"
        mock_openai_cls.assert_called_once_with(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
            timeout=60.0,
        )

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_provider_info(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        remote = OpenAIProvider.from_settings(
            AppSettings(
                openai_api_key="sk-test",
                openai_base_url="https://example.com",
                _env_file=None,  # type: ignore[call-arg]
            )
        )
        local = OllamaProvider.from_settings(AppSettings(_env_file=None))  # type: ignore[call-arg]

        remote_info = remote.info
        local_info = local.info

        assert isinstance(remote_info, ProviderInfo)
        assert remote_info.provider_mode == "openai-compatible"
        assert remote_info.provider_name == "openai-compatible"
        assert remote_info.as_dict()["chat_model"] == remote.chat_model
        assert local_info.provider_mode == "local"
        assert local_info.provider_name == "ollama"
        assert local_info.answer_model == local.chat_model

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_indexing_provider_uses_shared_settings(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        settings = AppSettings(
            openai_api_key="sk-test",
            indexing_embedding_model="text-embedding-3-large",
            indexing_embedding_base_url="https://index.example.com/v1",
            indexing_embedding_api_key="index-key",
            _env_file=None,  # type: ignore[call-arg]
        )

        provider = IndexingEmbeddingProvider.from_settings(settings)

        assert provider.embedding_model == "text-embedding-3-large"
        assert provider.base_url == "https://index.example.com/v1"
        mock_openai_cls.assert_called_once_with(
            api_key="index-key",
            base_url="https://index.example.com/v1",
            timeout=60.0,
        )

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_remote_socks_error_is_wrapped(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.side_effect = RuntimeError(
            "Using SOCKS proxy, but the 'socksio' package is not installed."
        )
        settings = AppSettings(
            openai_api_key="sk-test",
            openai_base_url="https://example.com/v1",
            _env_file=None,  # type: ignore[call-arg]
        )
        with pytest.raises(ProviderError, match="SOCKS proxy support is required"):
            OpenAIProvider.from_settings(settings)


class TestProviderRequests:
    @pytest.mark.parametrize(
        ("provider_factory", "expected_model"),
        [
            (_make_openai_provider, "text-embedding-3-small"),
            (_make_ollama_provider, "nomic-embed-text"),
        ],
    )
    def test_embed_texts_calls_client_with_correct_params(
        self,
        provider_factory: Any,
        expected_model: str,
    ) -> None:
        mock_client = MagicMock()
        provider = provider_factory(mock_client)
        provider.client.embeddings.create.return_value = _mock_embedding_response()

        result = provider.embed_texts(["hello", "world"])

        provider.client.embeddings.create.assert_called_once_with(
            input=["hello", "world"],
            model=expected_model,
        )
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    @pytest.mark.parametrize("provider_factory", [_make_openai_provider, _make_ollama_provider])
    def test_generate_grounded_answer_includes_calculation_trace(
        self,
        provider_factory: Any,
    ) -> None:
        mock_client = MagicMock()
        provider = provider_factory(mock_client)
        mock_choice = MagicMock()
        mock_choice.message.content = "The margin was 18.2%."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client.chat.completions.create.return_value = mock_response

        result = provider.generate_grounded_answer(
            question="Gross margin?",
            evidence="Some evidence",
            calculation_trace=["Step 1: Profit / Revenue", "Step 2: = 18.2%"],
        )

        assert result == "The margin was 18.2%."
        call_kwargs = provider.client.chat.completions.create.call_args
        user_content = call_kwargs.kwargs["messages"][1]["content"]
        assert "Step 1: Profit / Revenue" in user_content
        assert "Step 2: = 18.2%" in user_content

    def test_ollama_request_error_includes_startup_hint(self) -> None:
        import openai

        provider = _make_ollama_provider()
        provider.client.embeddings.create.side_effect = openai.OpenAIError("Connection refused")

        with pytest.raises(ProviderError, match="ollama serve"):
            provider.embed_texts(["test"])


class TestFakeClientRemoteExecution:
    """Run the remote pipeline with a fake OpenAI-compatible client."""

    def test_remote_pipeline_with_fake_client(self) -> None:
        from tesla_finrag.evaluation.workbench import (
            ProviderMode,
            WorkbenchPipeline,
            _seed_demo_repositories,
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
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
        mock_choice.message.content = "Based on the evidence, Tesla's revenue was significant."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        class FakeIndexingProvider:
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[float(index)] * 8 for index, _ in enumerate(texts)]

        provider = _make_openai_provider(mock_client)
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
            indexing_provider=FakeIndexingProvider(),
        )

        _, _, answer = pipeline.run("What was Tesla's 2023 revenue?")

        assert "evidence" in answer.answer_text.lower()
        assert answer.status.value == "ok"
        assert answer.retrieval_debug["provider_mode"] == "openai-compatible"
        assert answer.retrieval_debug["embedding_provider"] == "shared-indexing-backend"
        assert answer.retrieval_debug["answer_provider"] == "openai-compatible"
        assert answer.retrieval_debug["answer_model"] == "gpt-4o-mini"
        assert mock_client.embeddings.create.call_count == 0
        assert mock_client.chat.completions.create.call_count == 1

    def test_remote_pipeline_skips_chat_when_guardrail_fails(self) -> None:
        from tesla_finrag.evaluation.workbench import (
            ProviderMode,
            WorkbenchPipeline,
            _seed_demo_repositories,
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
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
        provider = _make_openai_provider(mock_client)

        class FakeIndexingProvider:
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[float(index)] * 8 for index, _ in enumerate(texts)]

        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
            indexing_provider=FakeIndexingProvider(),
        )

        _, _, answer = pipeline.run("What was Tesla's capital expenditure in FY2023?")

        assert answer.status.value == "insufficient_evidence"
        assert (
            "Insufficient evidence" in answer.answer_text
            or "Unable to provide a fully grounded answer" in answer.answer_text
        )
        assert answer.retrieval_debug["answer_provider"] == "template-guardrail"
        assert mock_client.chat.completions.create.call_count == 0
