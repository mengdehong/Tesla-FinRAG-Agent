"""Tests for AppSettings and get_settings() (tesla_finrag.settings)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesla_finrag.settings import AppSettings, get_settings


class TestAppSettings:
    def test_default_values_loaded(self) -> None:
        s = AppSettings()
        assert s.lancedb_uri == "data/processed/lancedb"
        assert Path(s.processed_data_dir).name == "processed"
        assert s.retrieval_top_k == 8
        assert s.rerank_top_k == 4
        assert s.log_level == "INFO"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RETRIEVAL_TOP_K", "20")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("PROCESSED_DATA_DIR", "/tmp/tesla-processed")
        s = AppSettings()
        assert s.retrieval_top_k == 20
        assert s.log_level == "DEBUG"
        assert s.processed_data_dir == "/tmp/tesla-processed"

    def test_retrieval_top_k_lower_bound(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(retrieval_top_k=0)

    def test_retrieval_top_k_upper_bound(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(retrieval_top_k=200)

    def test_extra_env_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Extra unknown env vars should not raise."""
        monkeypatch.setenv("TOTALLY_UNKNOWN_VAR", "value")
        s = AppSettings()
        assert s is not None


class TestGetSettings:
    def test_returns_app_settings_instance(self) -> None:
        s = get_settings()
        assert isinstance(s, AppSettings)

    def test_cached_singleton(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_cache_clear_reloads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_settings.cache_clear()
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        fresh = get_settings()
        assert fresh.log_level == "WARNING"
        # Restore to avoid polluting other tests
        get_settings.cache_clear()
