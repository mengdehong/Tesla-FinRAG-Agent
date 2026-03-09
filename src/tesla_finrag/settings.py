"""Structured application settings powered by pydantic-settings.

Reads configuration from environment variables and an optional ``.env`` file.
Import the singleton ``settings`` wherever configuration is needed; do not
instantiate ``AppSettings`` directly in application code.

Usage::

    from tesla_finrag.settings import settings

    uri = settings.lancedb_uri
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROCESSED_DIR = _PROJECT_ROOT / "data" / "processed"


class AppSettings(BaseSettings):
    """All tuneable parameters for the Tesla FinRAG agent.

    Values are read (in order of precedence):
    1. Environment variables
    2. ``.env`` file in the working directory
    3. Defaults declared here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM provider ──────────────────────────────────────────────────────────
    openai_api_key: str = Field("", description="OpenAI API key.")
    openai_model: str = Field(
        "gpt-4o-mini",
        description="Chat model used for answer generation.",
    )
    embedding_model: str = Field(
        "text-embedding-3-small",
        description="Model used to embed corpus chunks.",
    )
    openai_base_url: str | None = Field(
        None,
        description="Optional base URL for OpenAI-compatible API endpoints.",
    )
    openai_timeout_seconds: int = Field(
        60,
        ge=1,
        le=300,
        description="Request timeout in seconds for OpenAI API calls.",
    )
    ollama_base_url: str = Field(
        "http://localhost:11434/v1",
        description="Base URL for the local Ollama OpenAI-compatible endpoint.",
    )
    ollama_chat_model: str = Field(
        "qwen2.5:1.5b",
        description="Default Ollama chat model used for local answer generation.",
    )
    ollama_embedding_model: str = Field(
        "nomic-embed-text",
        description="Default Ollama embedding model used for local vectorization.",
    )
    ollama_timeout_seconds: int = Field(
        60,
        ge=1,
        le=300,
        description="Request timeout in seconds for Ollama API calls.",
    )

    # ── Vector / fact store ───────────────────────────────────────────────────
    processed_data_dir: str = Field(
        str(_DEFAULT_PROCESSED_DIR),
        description="Root directory containing processed runtime artifacts.",
    )
    lancedb_uri: str = Field(
        "data/processed/lancedb",
        description="File-system path or URI for the LanceDB database.",
    )
    indexing_embedding_model: str = Field(
        "nomic-embed-text",
        description="Embedding model used to index corpus chunks in LanceDB.",
    )
    indexing_embedding_base_url: str = Field(
        "http://localhost:11434/v1",
        description="Base URL for the embedding API used during indexing.",
    )
    indexing_embedding_api_key: str = Field(
        "",
        description=(
            "Optional API key for the shared indexing embedding backend. "
            "Defaults to an Ollama-safe placeholder when left blank for local indexing."
        ),
    )

    # ── Retrieval tuning ──────────────────────────────────────────────────────
    retrieval_top_k: int = Field(
        8,
        ge=1,
        le=100,
        description="Number of candidate chunks to retrieve per sub-question.",
    )
    rerank_top_k: int = Field(
        4,
        ge=1,
        le=50,
        description="Final number of chunks passed to the answer model after reranking.",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(
        "INFO",
        description="Root log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the cached singleton settings instance.

    Tests can call ``get_settings.cache_clear()`` to reload from env.
    """
    return AppSettings()


# Convenience singleton — the primary import target for application code.
settings: AppSettings = get_settings()
