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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── Vector / fact store ───────────────────────────────────────────────────
    lancedb_uri: str = Field(
        "data/processed/lancedb",
        description="File-system path or URI for the LanceDB database.",
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
