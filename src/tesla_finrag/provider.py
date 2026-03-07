"""OpenAI-compatible provider wrapper for embeddings and grounded answer narration.

Exposes ``embed_texts()`` and ``generate_grounded_answer()`` using the official
``openai`` SDK.  Configuration is read from :class:`AppSettings`.

Usage::

    from tesla_finrag.provider import OpenAIProvider

    provider = OpenAIProvider.from_settings()
    vectors = provider.embed_texts(["hello world"])
    answer  = provider.generate_grounded_answer(evidence="...", question="...")
"""

from __future__ import annotations

from dataclasses import dataclass, field

import openai

from tesla_finrag.settings import AppSettings, get_settings


class ProviderError(Exception):
    """Raised when a remote provider call fails or is misconfigured."""


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata about the provider configuration used for diagnostics."""

    provider_mode: str
    embedding_model: str
    answer_model: str
    base_url: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_mode": self.provider_mode,
            "embedding_model": self.embedding_model,
            "answer_model": self.answer_model,
            # Keep the legacy key during the transition so old callers do not break.
            "chat_model": self.answer_model,
            "base_url": self.base_url,
        }


_GROUNDED_ANSWER_SYSTEM_PROMPT = (
    "You are a financial analyst assistant. You MUST answer the user's question "
    "using ONLY the provided evidence. Do NOT add information beyond what the "
    "evidence contains. Be concise and factual."
)


@dataclass
class OpenAIProvider:
    """Thin wrapper around the OpenAI SDK for embeddings and chat.

    Parameters:
        client: An ``openai.OpenAI`` synchronous client instance.
        embedding_model: Model name for embedding requests.
        chat_model: Model name for chat completion requests.
        base_url: The base URL used (for diagnostics).
    """

    client: openai.OpenAI
    embedding_model: str
    chat_model: str
    base_url: str | None = field(default=None)

    @classmethod
    def from_settings(cls, settings: AppSettings | None = None) -> OpenAIProvider:
        """Create a provider from application settings.

        Raises:
            ProviderError: If ``openai_api_key`` is empty.
        """
        s = settings or get_settings()
        if not s.openai_api_key:
            raise ProviderError(
                "OpenAI API key is required for openai-compatible provider mode. "
                "Set the OPENAI_API_KEY environment variable."
            )

        client = openai.OpenAI(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            timeout=float(s.openai_timeout_seconds),
        )
        return cls(
            client=client,
            embedding_model=s.embedding_model,
            chat_model=s.openai_model,
            base_url=s.openai_base_url,
        )

    @property
    def info(self) -> ProviderInfo:
        """Return provider metadata for diagnostics."""
        return ProviderInfo(
            provider_mode="openai-compatible",
            embedding_model=self.embedding_model,
            answer_model=self.chat_model,
            base_url=self.base_url,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the configured embedding model.

        Args:
            texts: Strings to embed.

        Returns:
            A list of embedding vectors (one per input text).

        Raises:
            ProviderError: If the API call fails.
        """
        if not texts:
            return []
        try:
            response = self.client.embeddings.create(
                input=texts,
                model=self.embedding_model,
            )
        except openai.OpenAIError as exc:
            raise ProviderError(f"Embedding request failed: {exc}") from exc

        # Sort by index to preserve input order
        sorted_data = sorted(response.data, key=lambda d: d.index)
        return [item.embedding for item in sorted_data]

    def generate_grounded_answer(
        self,
        question: str,
        evidence: str,
        calculation_trace: list[str] | None = None,
    ) -> str:
        """Generate a grounded answer using the configured chat model.

        The model receives the evidence and calculation trace as context and
        produces only the ``answer_text``.  Status, citations, confidence, and
        calculation trace remain locally computed.

        Args:
            question: The user's original question.
            evidence: Pre-assembled evidence summary text.
            calculation_trace: Optional calculation steps to include.

        Returns:
            The narrated answer text.

        Raises:
            ProviderError: If the API call fails.
        """
        user_parts = [f"Question: {question}", "", "Evidence:", evidence]
        if calculation_trace:
            user_parts.extend(["", "Calculation steps:"])
            user_parts.extend(f"- {step}" for step in calculation_trace)
        user_parts.extend(["", "Provide a concise answer based solely on the evidence above."])
        user_message = "\n".join(user_parts)

        try:
            response = self.client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": _GROUNDED_ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
            )
        except openai.OpenAIError as exc:
            raise ProviderError(f"Chat completion request failed: {exc}") from exc

        choice = response.choices[0]
        return (choice.message.content or "").strip()
