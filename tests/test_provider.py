"""Tests for the OpenAI-compatible provider wrapper (tesla_finrag.provider)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tesla_finrag.provider import OpenAIProvider, ProviderError, ProviderInfo
from tesla_finrag.settings import AppSettings

# ---------------------------------------------------------------------------
# Settings extension tests
# ---------------------------------------------------------------------------


class TestSettingsProviderFields:
    """Verify the new OPENAI_BASE_URL and OPENAI_TIMEOUT_SECONDS fields."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        s = AppSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_base_url is None
        assert s.openai_timeout_seconds == 60

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "https://my-proxy.example.com/v1")
        monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "60")
        s = AppSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_base_url == "https://my-proxy.example.com/v1"
        assert s.openai_timeout_seconds == 60

    def test_timeout_lower_bound(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(openai_timeout_seconds=0)

    def test_timeout_upper_bound(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(openai_timeout_seconds=500)


# ---------------------------------------------------------------------------
# Provider construction tests
# ---------------------------------------------------------------------------


class TestProviderFromSettings:
    def test_missing_api_key_raises(self) -> None:
        settings = AppSettings(openai_api_key="", _env_file=None)  # type: ignore[call-arg]
        with pytest.raises(ProviderError, match="API key is required"):
            OpenAIProvider.from_settings(settings)

    @patch("tesla_finrag.provider.openai.OpenAI")
    def test_valid_settings_creates_provider(self, mock_openai_cls: MagicMock) -> None:
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
    def test_provider_info(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        settings = AppSettings(
            openai_api_key="sk-test",
            openai_base_url="https://example.com",
            _env_file=None,  # type: ignore[call-arg]
        )
        provider = OpenAIProvider.from_settings(settings)
        info = provider.info
        assert isinstance(info, ProviderInfo)
        assert info.provider_mode == "openai-compatible"
        assert info.embedding_model == settings.embedding_model
        assert info.answer_model == settings.openai_model
        assert info.as_dict()["chat_model"] == settings.openai_model


# ---------------------------------------------------------------------------
# Embedding request wiring tests (fake client)
# ---------------------------------------------------------------------------


class TestEmbedTexts:
    def _make_provider(self) -> OpenAIProvider:
        mock_client = MagicMock()
        return OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )

    def test_empty_input_returns_empty(self) -> None:
        provider = self._make_provider()
        assert provider.embed_texts([]) == []

    def test_calls_client_with_correct_params(self) -> None:
        provider = self._make_provider()
        # Mock the embedding response
        mock_item_1 = MagicMock()
        mock_item_1.index = 0
        mock_item_1.embedding = [0.1, 0.2, 0.3]
        mock_item_2 = MagicMock()
        mock_item_2.index = 1
        mock_item_2.embedding = [0.4, 0.5, 0.6]

        mock_response = MagicMock()
        mock_response.data = [mock_item_2, mock_item_1]  # Out of order

        provider.client.embeddings.create.return_value = mock_response

        result = provider.embed_texts(["hello", "world"])

        provider.client.embeddings.create.assert_called_once_with(
            input=["hello", "world"],
            model="text-embedding-3-small",
        )
        # Results should be sorted by index
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    def test_api_error_raises_provider_error(self) -> None:
        import openai

        provider = self._make_provider()
        provider.client.embeddings.create.side_effect = openai.OpenAIError("rate limit")

        with pytest.raises(ProviderError, match="Embedding request failed"):
            provider.embed_texts(["test"])


# ---------------------------------------------------------------------------
# Chat request wiring tests (fake client)
# ---------------------------------------------------------------------------


class TestGenerateGroundedAnswer:
    def _make_provider(self) -> OpenAIProvider:
        mock_client = MagicMock()
        return OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )

    def test_calls_client_with_system_and_user_messages(self) -> None:
        provider = self._make_provider()
        mock_choice = MagicMock()
        mock_choice.message.content = "Tesla's revenue was $96.77B in 2023."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client.chat.completions.create.return_value = mock_response

        result = provider.generate_grounded_answer(
            question="What was Tesla's 2023 revenue?",
            evidence="Total Revenues: 96,773,000,000 USD (period ending 2023-12-31)",
        )

        assert result == "Tesla's revenue was $96.77B in 2023."

        call_kwargs = provider.client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "2023 revenue" in messages[1]["content"]
        assert "96,773,000,000" in messages[1]["content"]

    def test_includes_calculation_trace(self) -> None:
        provider = self._make_provider()
        mock_choice = MagicMock()
        mock_choice.message.content = "The margin was 18.2%."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client.chat.completions.create.return_value = mock_response

        provider.generate_grounded_answer(
            question="Gross margin?",
            evidence="Some evidence",
            calculation_trace=["Step 1: Profit / Revenue", "Step 2: = 18.2%"],
        )

        call_kwargs = provider.client.chat.completions.create.call_args
        user_content = call_kwargs.kwargs["messages"][1]["content"]
        assert "Step 1: Profit / Revenue" in user_content
        assert "Step 2: = 18.2%" in user_content

    def test_api_error_raises_provider_error(self) -> None:
        import openai

        provider = self._make_provider()
        provider.client.chat.completions.create.side_effect = openai.OpenAIError("timeout")

        with pytest.raises(ProviderError, match="Chat completion request failed"):
            provider.generate_grounded_answer(
                question="Test?",
                evidence="evidence",
            )


# ---------------------------------------------------------------------------
# Fake-client remote execution (end-to-end with mocked OpenAI)
# ---------------------------------------------------------------------------


class TestFakeClientRemoteExecution:
    """Run the full remote pipeline with a fake OpenAI client."""

    def test_remote_pipeline_with_fake_client(self) -> None:
        from tesla_finrag.evaluation.workbench import (
            ProviderMode,
            WorkbenchPipeline,
            _seed_demo_repositories,
        )

        corpus_repo, facts_repo = _seed_demo_repositories()

        # Build a provider with a fully mocked client
        mock_client = MagicMock()

        # Mock embeddings: return a fixed-dimension vector for each input
        def fake_embed(input, model):  # noqa: A002
            mock_response = MagicMock()
            items = []
            for i, _ in enumerate(input):
                item = MagicMock()
                item.index = i
                item.embedding = [float(i)] * 8
                items.append(item)
            mock_response.data = items
            return mock_response

        mock_client.embeddings.create.side_effect = fake_embed

        # Mock chat: return a canned answer
        mock_choice = MagicMock()
        mock_choice.message.content = "Based on the evidence, Tesla's revenue was significant."
        mock_chat_response = MagicMock()
        mock_chat_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_chat_response

        provider = OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )

        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
        )

        plan, bundle, answer = pipeline.run("What was Tesla's 2023 revenue?")

        # Answer text should come from the mocked chat model
        assert "evidence" in answer.answer_text.lower()
        assert answer.status.value == "ok"

        # Diagnostics should report remote mode
        assert answer.retrieval_debug["provider_mode"] == "openai-compatible"
        assert answer.retrieval_debug["embedding_model"] == "text-embedding-3-small"
        assert answer.retrieval_debug["answer_model"] == "gpt-4o-mini"
        assert answer.retrieval_debug["chat_model"] == "gpt-4o-mini"
        assert answer.retrieval_debug["embedding_provider"] == "openai-compatible"
        assert answer.retrieval_debug["answer_provider"] == "openai-compatible"

        # The embedding API should have been called
        assert mock_client.embeddings.create.call_count >= 1
        # The chat API should have been called exactly once
        assert mock_client.chat.completions.create.call_count == 1

    def test_remote_pipeline_skips_chat_when_local_guardrail_fails(self) -> None:
        from tesla_finrag.evaluation.workbench import (
            ProviderMode,
            WorkbenchPipeline,
            _seed_demo_repositories,
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
        mock_client = MagicMock()

        def fake_embed(input, model):  # noqa: A002
            mock_response = MagicMock()
            items = []
            for i, _ in enumerate(input):
                item = MagicMock()
                item.index = i
                item.embedding = [float(i)] * 8
                items.append(item)
            mock_response.data = items
            return mock_response

        mock_client.embeddings.create.side_effect = fake_embed

        provider = OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
        )

        _, _, answer = pipeline.run("What was Tesla's capital expenditure in FY2023?")

        assert answer.status.value == "insufficient_evidence"
        assert "Insufficient evidence" in answer.answer_text
        assert answer.retrieval_debug["answer_provider"] == "template-guardrail"
        assert mock_client.chat.completions.create.call_count == 0

    def test_remote_vector_index_is_cached_per_scope(self) -> None:
        from tesla_finrag.evaluation.workbench import (
            ProviderMode,
            WorkbenchPipeline,
            _seed_demo_repositories,
        )

        corpus_repo, facts_repo = _seed_demo_repositories()
        mock_client = MagicMock()

        def fake_embed(input, model):  # noqa: A002
            mock_response = MagicMock()
            items = []
            for i, _ in enumerate(input):
                item = MagicMock()
                item.index = i
                item.embedding = [float(i)] * 8
                items.append(item)
            mock_response.data = items
            return mock_response

        mock_client.embeddings.create.side_effect = fake_embed
        mock_choice = MagicMock()
        mock_choice.message.content = "Grounded remote answer."
        mock_chat_response = MagicMock()
        mock_chat_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_chat_response

        provider = OpenAIProvider(
            client=mock_client,
            embedding_model="text-embedding-3-small",
            chat_model="gpt-4o-mini",
        )
        pipeline = WorkbenchPipeline(
            corpus_repo=corpus_repo,
            facts_repo=facts_repo,
            provider_mode=ProviderMode.OPENAI_COMPATIBLE,
            provider=provider,
        )

        pipeline.run("What was Tesla's 2023 revenue?")
        pipeline.run("What was Tesla's 2023 revenue?")

        # First run: corpus embeddings + query embedding. Second run: query embedding only.
        assert mock_client.embeddings.create.call_count == 3
