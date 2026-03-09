"""Tests for the i18n module."""

from __future__ import annotations

from tesla_finrag.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    concept_label,
    response_language_directive,
    t,
)


class TestTranslationFunction:
    """Tests for the ``t()`` translation helper."""

    def test_english_default(self) -> None:
        result = t("en", "app_title")
        assert "Tesla FinRAG" in result

    def test_chinese_locale(self) -> None:
        result = t("zh_CN", "app_title")
        assert "Tesla" in result
        assert "RAG" in result
        # Should contain Chinese characters
        assert any("\u4e00" <= ch <= "\u9fff" for ch in result)

    def test_unknown_key_returns_key(self) -> None:
        result = t("en", "nonexistent_key_12345")
        assert result == "nonexistent_key_12345"

    def test_unknown_locale_falls_back_to_english(self) -> None:
        result = t("fr_FR", "app_title")
        assert result == t("en", "app_title")

    def test_format_substitution(self) -> None:
        """If a translation contained format placeholders, kwargs are applied."""
        # The current translations do not use placeholders, but the function
        # supports them.  Verify the passthrough is safe.
        result = t("en", "app_title", foo="bar")
        assert "Tesla FinRAG" in result

    def test_all_sidebar_keys_present_in_both_locales(self) -> None:
        sidebar_keys = [
            "sidebar_runtime",
            "sidebar_provider",
            "sidebar_filing_scope",
            "sidebar_fiscal_year",
            "sidebar_filing_type",
            "sidebar_quarter",
            "sidebar_language",
        ]
        for key in sidebar_keys:
            for locale in SUPPORTED_LOCALES:
                result = t(locale, key)
                assert result != key, f"Missing translation for {key} in {locale}"


class TestConceptLabel:
    """Tests for the ``concept_label()`` helper."""

    def test_known_concept_english(self) -> None:
        assert concept_label("en", "us-gaap:Revenues") == "Total Revenue"

    def test_known_concept_chinese(self) -> None:
        label = concept_label("zh_CN", "us-gaap:Revenues")
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label)

    def test_unknown_concept_camel_case_split(self) -> None:
        label = concept_label("en", "us-gaap:SomeNewConcept")
        assert "Some" in label
        assert "New" in label
        assert "Concept" in label

    def test_unknown_concept_no_namespace(self) -> None:
        label = concept_label("en", "PlainConcept")
        assert "Plain" in label
        assert "Concept" in label


class TestResponseLanguageDirective:
    """Tests for the ``response_language_directive()`` helper."""

    def test_english_returns_none(self) -> None:
        assert response_language_directive("en") is None

    def test_chinese_returns_directive(self) -> None:
        directive = response_language_directive("zh_CN")
        assert directive is not None
        assert "Simplified Chinese" in directive
        assert "简体中文" in directive

    def test_unknown_locale_returns_none(self) -> None:
        assert response_language_directive("fr_FR") is None


class TestConstants:
    """Tests for module-level constants."""

    def test_supported_locales(self) -> None:
        assert "en" in SUPPORTED_LOCALES
        assert "zh_CN" in SUPPORTED_LOCALES
        assert len(SUPPORTED_LOCALES) == 2

    def test_default_locale(self) -> None:
        assert DEFAULT_LOCALE == "en"
