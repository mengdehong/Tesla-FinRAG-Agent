"""Provider wrappers for shared indexing, local Ollama, and remote execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import openai

from tesla_finrag.settings import AppSettings, get_settings


class ProviderError(Exception):
    """Raised when a provider call fails or is misconfigured."""


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata about the provider configuration used for diagnostics."""

    provider_mode: str
    provider_name: str
    embedding_model: str
    answer_model: str
    base_url: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_mode": self.provider_mode,
            "provider_name": self.provider_name,
            "embedding_model": self.embedding_model,
            "answer_model": self.answer_model,
            # Keep the legacy key during the transition so old callers do not break.
            "chat_model": self.answer_model,
            "base_url": self.base_url,
        }


@dataclass(frozen=True)
class EmbeddingProviderInfo:
    """Metadata about the embedding backend used for the shared vector index."""

    provider_name: str
    embedding_model: str
    base_url: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "embedding_model": self.embedding_model,
            "base_url": self.base_url,
        }


class GroundedAnswerProvider(Protocol):
    """Small runtime contract shared by local and remote providers."""

    @property
    def info(self) -> ProviderInfo: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def generate_grounded_answer(
        self,
        question: str,
        evidence: str,
        calculation_trace: list[str] | None = None,
    ) -> str: ...


class TextEmbeddingProvider(Protocol):
    """Small runtime contract for shared query/index embedding backends."""

    @property
    def info(self) -> EmbeddingProviderInfo: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


_GROUNDED_ANSWER_SYSTEM_PROMPT = (
    "You are a financial analyst assistant. You MUST answer the user's question "
    "using ONLY the provided evidence. Do NOT add information beyond what the "
    "evidence contains. Be concise and factual."
)
_SOCKS_ERROR_HINT = (
    "SOCKS proxy support is required for openai-compatible mode. "
    "Sync the project dependencies so `httpx[socks]` is installed."
)
_OLLAMA_STARTUP_HINT = (
    "Ensure `ollama serve` is running and the configured local models are "
    "available via `ollama pull`."
)
_INDEXING_HINT = (
    "Ensure the shared indexing embedding backend is reachable and configured "
    "consistently for both ingestion and runtime query execution."
)


def _iter_error_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _contains_socks_error(exc: BaseException) -> bool:
    for item in _iter_error_chain(exc):
        message = f"{type(item).__name__}: {item}".lower()
        if "socks" in message or "socksio" in message:
            return True
    return False


def _normalize_provider_error(
    *,
    provider_name: str,
    action: str,
    exc: Exception,
    default_hint: str | None = None,
) -> ProviderError:
    detail = str(exc).strip() or exc.__class__.__name__
    message = f"{provider_name} provider failed to {action}: {detail}"
    if _contains_socks_error(exc):
        message = f"{message} {_SOCKS_ERROR_HINT}"
    elif default_hint:
        message = f"{message} {default_hint}"
    return ProviderError(message)


def _looks_like_ollama_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return False
    lowered = base_url.lower()
    return (
        "localhost:11434" in lowered
        or "127.0.0.1:11434" in lowered
        or "ollama" in lowered
    )


def _resolve_api_key(
    *,
    explicit_api_key: str,
    base_url: str | None,
    fallback_api_key: str,
    purpose: str,
) -> str:
    if explicit_api_key:
        return explicit_api_key
    if _looks_like_ollama_endpoint(base_url):
        return "ollama"
    if fallback_api_key:
        return fallback_api_key
    raise ProviderError(
        f"{purpose} API key is required for non-local embedding backends. "
        "Set INDEXING_EMBEDDING_API_KEY or OPENAI_API_KEY."
    )


def _embed_texts(
    *,
    client: openai.OpenAI,
    embedding_model: str,
    texts: list[str],
    provider_name: str,
) -> list[list[float]]:
    if not texts:
        return []
    try:
        response = client.embeddings.create(
            input=texts,
            model=embedding_model,
        )
    except Exception as exc:  # pragma: no cover - error types vary by SDK transport
        raise _normalize_provider_error(
            provider_name=provider_name,
            action="run an embedding request",
            exc=exc,
            default_hint=(
                _OLLAMA_STARTUP_HINT if provider_name == "ollama" else None
            ),
        ) from exc

    sorted_data = sorted(response.data, key=lambda d: d.index)
    return [item.embedding for item in sorted_data]


def _generate_grounded_answer(
    *,
    client: openai.OpenAI,
    chat_model: str,
    question: str,
    evidence: str,
    calculation_trace: list[str] | None,
    provider_name: str,
) -> str:
    user_parts = [f"Question: {question}", "", "Evidence:", evidence]
    if calculation_trace:
        user_parts.extend(["", "Calculation steps:"])
        user_parts.extend(f"- {step}" for step in calculation_trace)
    user_parts.extend(["", "Provide a concise answer based solely on the evidence above."])
    user_message = "\n".join(user_parts)

    try:
        response = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": _GROUNDED_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - error types vary by SDK transport
        raise _normalize_provider_error(
            provider_name=provider_name,
            action="run a chat completion request",
            exc=exc,
            default_hint=(
                _OLLAMA_STARTUP_HINT if provider_name == "ollama" else None
            ),
        ) from exc

    choice = response.choices[0]
    return (choice.message.content or "").strip()


@dataclass
class IndexingEmbeddingProvider:
    """Shared embedding backend used by both ingest and runtime retrieval."""

    client: openai.OpenAI
    embedding_model: str
    base_url: str
    provider_name: str = "shared-indexing-backend"

    @classmethod
    def from_settings(
        cls, settings: AppSettings | None = None
    ) -> IndexingEmbeddingProvider:
        s = settings or get_settings()
        api_key = _resolve_api_key(
            explicit_api_key=s.indexing_embedding_api_key,
            base_url=s.indexing_embedding_base_url,
            fallback_api_key=s.openai_api_key,
            purpose="Indexing embedding",
        )

        try:
            client = openai.OpenAI(
                api_key=api_key,
                base_url=s.indexing_embedding_base_url,
                timeout=float(s.openai_timeout_seconds),
            )
        except Exception as exc:  # pragma: no cover - transport errors are environment-specific
            raise _normalize_provider_error(
                provider_name="shared-indexing-backend",
                action="initialize the client",
                exc=exc,
                default_hint=(
                    _OLLAMA_STARTUP_HINT
                    if _looks_like_ollama_endpoint(s.indexing_embedding_base_url)
                    else _INDEXING_HINT
                ),
            ) from exc

        return cls(
            client=client,
            embedding_model=s.indexing_embedding_model,
            base_url=s.indexing_embedding_base_url,
        )

    @property
    def info(self) -> EmbeddingProviderInfo:
        return EmbeddingProviderInfo(
            provider_name=self.provider_name,
            embedding_model=self.embedding_model,
            base_url=self.base_url,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return _embed_texts(
            client=self.client,
            embedding_model=self.embedding_model,
            texts=texts,
            provider_name=self.info.provider_name,
        )


@dataclass
class OpenAIProvider:
    """Thin wrapper around the OpenAI SDK for remote execution."""

    client: openai.OpenAI
    embedding_model: str
    chat_model: str
    base_url: str | None = field(default=None)

    @classmethod
    def from_settings(cls, settings: AppSettings | None = None) -> OpenAIProvider:
        """Create a remote provider from application settings."""
        s = settings or get_settings()
        if not s.openai_api_key:
            raise ProviderError(
                "OpenAI API key is required for openai-compatible provider mode. "
                "Set the OPENAI_API_KEY environment variable."
            )

        try:
            client = openai.OpenAI(
                api_key=s.openai_api_key,
                base_url=s.openai_base_url,
                timeout=float(s.openai_timeout_seconds),
            )
        except Exception as exc:  # pragma: no cover - transport errors are environment-specific
            raise _normalize_provider_error(
                provider_name="openai-compatible",
                action="initialize the client",
                exc=exc,
            ) from exc

        return cls(
            client=client,
            embedding_model=s.embedding_model,
            chat_model=s.openai_model,
            base_url=s.openai_base_url,
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_mode="openai-compatible",
            provider_name="openai-compatible",
            embedding_model=self.embedding_model,
            answer_model=self.chat_model,
            base_url=self.base_url,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return _embed_texts(
            client=self.client,
            embedding_model=self.embedding_model,
            texts=texts,
            provider_name=self.info.provider_name,
        )

    def generate_grounded_answer(
        self,
        question: str,
        evidence: str,
        calculation_trace: list[str] | None = None,
    ) -> str:
        return _generate_grounded_answer(
            client=self.client,
            chat_model=self.chat_model,
            question=question,
            evidence=evidence,
            calculation_trace=calculation_trace,
            provider_name=self.info.provider_name,
        )


@dataclass
class OllamaProvider:
    """Thin wrapper around the OpenAI SDK for local Ollama execution."""

    client: openai.OpenAI
    embedding_model: str
    chat_model: str
    base_url: str

    @classmethod
    def from_settings(cls, settings: AppSettings | None = None) -> OllamaProvider:
        """Create a local Ollama provider from application settings."""
        s = settings or get_settings()

        try:
            client = openai.OpenAI(
                api_key="ollama",
                base_url=s.ollama_base_url,
                timeout=float(s.ollama_timeout_seconds),
            )
        except Exception as exc:  # pragma: no cover - transport errors are environment-specific
            raise _normalize_provider_error(
                provider_name="ollama",
                action="initialize the client",
                exc=exc,
                default_hint=_OLLAMA_STARTUP_HINT,
            ) from exc

        return cls(
            client=client,
            embedding_model=s.ollama_embedding_model,
            chat_model=s.ollama_chat_model,
            base_url=s.ollama_base_url,
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_mode="local",
            provider_name="ollama",
            embedding_model=self.embedding_model,
            answer_model=self.chat_model,
            base_url=self.base_url,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return _embed_texts(
            client=self.client,
            embedding_model=self.embedding_model,
            texts=texts,
            provider_name=self.info.provider_name,
        )

    def generate_grounded_answer(
        self,
        question: str,
        evidence: str,
        calculation_trace: list[str] | None = None,
    ) -> str:
        return _generate_grounded_answer(
            client=self.client,
            chat_model=self.chat_model,
            question=question,
            evidence=evidence,
            calculation_trace=calculation_trace,
            provider_name=self.info.provider_name,
        )
