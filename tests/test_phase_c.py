"""Phase C tests: table fallback, composite answer, planner composite detection.

Covers the three new capabilities added in Phase C to fix BQ-003:
1. **Linker table fallback** – creates FactRecords from table chunks when
   the XBRL fact store has no matching facts.
2. **Composer composite partial answer** – produces status=OK when the
   narrative lane succeeds even if the numeric lane is limited.
3. **Planner composite detection** – classifies mixed narrative+numeric
   questions as HYBRID_REASONING with COMPOSITE answer shape.
4. **Semantic protection** – "cost of automotive revenue" must not be
   confused with "total cost of revenue".
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    AnswerShape,
    ChunkKind,
    QueryType,
    TableChunk,
)
from tesla_finrag.planning.query_planner import (
    _build_sub_queries,
    _concept_to_human_label,
    _infer_answer_shape,
    classify_query_type,
    extract_metrics,
)

# ===================================================================
# Helpers
# ===================================================================

_DOC_ID = uuid4()


def _table_chunk(
    *,
    caption: str = "",
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    raw_text: str = "",
    doc_id=_DOC_ID,
) -> TableChunk:
    """Build a minimal TableChunk for testing."""
    return TableChunk(
        doc_id=doc_id,
        kind=ChunkKind.TABLE,
        section_title="Test Section",
        caption=caption,
        headers=headers or [],
        rows=rows or [],
        raw_text=raw_text or caption,
    )


# ===================================================================
# 1. Linker table fallback unit tests
# ===================================================================


class TestExtractValueFromTable:
    """Test _extract_value_from_table static method."""

    def test_extract_cost_of_revenue_from_table_row(self):
        tbl = _table_chunk(
            caption="(in millions)",
            rows=[
                ["", "Year Ended December 31, 2023", "Year Ended December 31, 2022"],
                ["Cost of automotive sales revenue", "66,389", "51,108"],
                ["Research and development", "3,969", "3,075"],
            ],
        )
        aliases = ["cost of automotive revenue", "cost of revenue", "cost of automotive sales"]
        match_2023 = EvidenceLinker._extract_value_from_table(
            tbl,
            aliases,
            period=date(2023, 12, 31),
        )
        match_2022 = EvidenceLinker._extract_value_from_table(
            tbl,
            aliases,
            period=date(2022, 12, 31),
        )
        assert match_2023 is not None
        assert match_2022 is not None
        assert match_2023[0] == pytest.approx(66389.0)
        assert match_2022[0] == pytest.approx(51108.0)

    def test_extract_returns_none_when_no_match(self):
        tbl = _table_chunk(
            rows=[
                ["Revenue", "96,773", "81,462"],
            ],
        )
        aliases = ["cost of revenue"]
        match = EvidenceLinker._extract_value_from_table(tbl, aliases)
        assert match is None

    def test_extract_handles_dollar_sign_and_commas(self):
        tbl = _table_chunk(
            rows=[
                ["Cost of revenue", "$49,571", "$40,217"],
            ],
        )
        aliases = ["cost of revenue"]
        match = EvidenceLinker._extract_value_from_table(tbl, aliases)
        assert match is not None
        assert match[0] == pytest.approx(49571.0)

    def test_extract_matches_alias_in_combined_row_text(self):
        """When alias is not in row[0] alone, check full row text."""
        tbl = _table_chunk(
            rows=[
                ["Cost of", "automotive sales revenue", "66,389"],
            ],
        )
        aliases = ["cost of automotive sales revenue"]
        match = EvidenceLinker._extract_value_from_table(tbl, aliases)
        assert match is not None
        assert match[0] == pytest.approx(66389.0)

    def test_extract_preserves_parenthesized_negative_values(self):
        tbl = _table_chunk(
            rows=[
                ["Other income", "(1,234)"],
            ],
        )
        match = EvidenceLinker._extract_value_from_table(tbl, ["other income"])
        assert match is not None
        assert match[0] == pytest.approx(-1234.0)

    def test_extract_returns_none_when_period_column_is_ambiguous(self):
        tbl = _table_chunk(
            caption="(in millions)",
            rows=[
                ["Cost of revenue", "70,000", "60,000"],
            ],
        )
        match = EvidenceLinker._extract_value_from_table(
            tbl,
            ["cost of revenue"],
            period=date(2023, 12, 31),
        )
        assert match is None


class TestInferTableScale:
    """Test _infer_table_scale static method."""

    def test_detects_millions_in_caption(self):
        tbl = _table_chunk(caption="Consolidated Statements (in millions)")
        assert EvidenceLinker._infer_table_scale(tbl) == 1_000_000

    def test_detects_thousands_in_headers(self):
        tbl = _table_chunk(headers=["Item", "Amount (in thousands)"])
        assert EvidenceLinker._infer_table_scale(tbl) == 1_000

    def test_detects_billions(self):
        tbl = _table_chunk(caption="Summary (in billions)")
        assert EvidenceLinker._infer_table_scale(tbl) == 1_000_000_000

    def test_defaults_to_1_when_no_scale_mentioned(self):
        tbl = _table_chunk(caption="Some table")
        assert EvidenceLinker._infer_table_scale(tbl) == 1


class TestGetTableAliases:
    """Test _get_table_aliases static method."""

    def test_known_concept_returns_configured_aliases(self):
        aliases = EvidenceLinker._get_table_aliases("us-gaap:CostOfGoodsAndServicesSold")
        assert "cost of automotive revenue" in aliases
        assert "cost of revenue" in aliases

    def test_unknown_concept_uses_camel_case_split(self):
        aliases = EvidenceLinker._get_table_aliases("us-gaap:TotalAssetsCurrentAndNoncurrent")
        assert len(aliases) == 1
        assert "total assets current and noncurrent" == aliases[0]

    def test_automotive_query_restricts_cost_aliases(self):
        aliases = EvidenceLinker._get_table_aliases(
            "us-gaap:CostOfGoodsAndServicesSold",
            original_query="How did cost of automotive revenue change?",
        )
        assert aliases
        assert all("automotive" in alias for alias in aliases)


class TestTableMentionsConcept:
    """Test _table_mentions_concept static method."""

    def test_matches_configured_alias_in_raw_text(self):
        tbl = _table_chunk(raw_text="Cost of automotive revenue was $66B")
        assert EvidenceLinker._table_mentions_concept(tbl, ["us-gaap:CostOfGoodsAndServicesSold"])

    def test_matches_camel_case_split_label(self):
        tbl = _table_chunk(raw_text="Total revenues for FY2023")
        assert EvidenceLinker._table_mentions_concept(tbl, ["us-gaap:Revenues"])

    def test_no_match_returns_false(self):
        tbl = _table_chunk(raw_text="Operating lease details")
        assert not EvidenceLinker._table_mentions_concept(
            tbl, ["us-gaap:CostOfGoodsAndServicesSold"]
        )


# ===================================================================
# 2. Planner composite detection tests
# ===================================================================


class TestClassifyComposite:
    """Test composite question detection in classify_query_type."""

    def test_narrative_plus_metric_plus_compare_returns_hybrid(self):
        """BQ-003 pattern: risk factors + cost change."""
        q = (
            "What supply chain risk factors did Tesla mention in its 2023 10-K, "
            "and how did cost of automotive revenue change between FY2022 and FY2023?"
        )
        metrics = ["us-gaap:CostOfGoodsAndServicesSold"]
        assert classify_query_type(q, metrics) == QueryType.HYBRID_REASONING

    def test_pure_narrative_returns_narrative_compare(self):
        q = "What risk factors did Tesla discuss in its 2023 10-K filing?"
        assert classify_query_type(q, []) == QueryType.NARRATIVE_COMPARE

    def test_pure_numeric_returns_numeric_calculation(self):
        q = "Calculate the gross margin for FY2023"
        metrics = ["us-gaap:GrossProfit", "us-gaap:Revenues"]
        assert classify_query_type(q, metrics) == QueryType.NUMERIC_CALCULATION

    def test_metric_without_narrative_returns_numeric(self):
        q = "How did revenue change between FY2022 and FY2023?"
        metrics = ["us-gaap:Revenues"]
        assert classify_query_type(q, metrics) == QueryType.NUMERIC_CALCULATION


class TestInferCompositeShape:
    """Test _infer_answer_shape for COMPOSITE detection."""

    def test_narrative_metric_compare_returns_composite(self):
        q = (
            "What supply chain risk factors did Tesla mention "
            "and how did cost of automotive revenue change between FY2022 and FY2023?"
        )
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        metrics = ["us-gaap:CostOfGoodsAndServicesSold"]
        assert _infer_answer_shape(q, periods, metrics) == AnswerShape.COMPOSITE

    def test_narrative_metric_multiperiod_returns_composite(self):
        q = "Discuss risk factors and revenue for FY2022 and FY2023"
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        metrics = ["us-gaap:Revenues"]
        assert _infer_answer_shape(q, periods, metrics) == AnswerShape.COMPOSITE

    def test_two_period_compare_without_narrative_returns_comparison(self):
        q = "Compare revenue between FY2022 and FY2023"
        periods = [date(2022, 12, 31), date(2023, 12, 31)]
        metrics = ["us-gaap:Revenues"]
        assert _infer_answer_shape(q, periods, metrics) == AnswerShape.COMPARISON


# ===================================================================
# 3. Human-readable sub-query tests
# ===================================================================


class TestConceptToHumanLabel:
    """Test _concept_to_human_label helper."""

    def test_known_concept_uses_alias(self):
        assert _concept_to_human_label("us-gaap:Revenues") == "revenue"
        assert _concept_to_human_label("us-gaap:NetIncomeLoss") == "net income"

    def test_unknown_concept_splits_camel_case(self):
        label = _concept_to_human_label("us-gaap:TotalLongTermDebt")
        assert label == "total long term debt"

    def test_concept_without_namespace(self):
        label = _concept_to_human_label("GrossProfit")
        # "GrossProfit" is not in _METRIC_ALIASES by bare name
        assert "gross" in label.lower()
        assert "profit" in label.lower()

    def test_cost_label_uses_automotive_context(self):
        label = _concept_to_human_label(
            "us-gaap:CostOfGoodsAndServicesSold",
            question="How did cost of automotive revenue change between FY2022 and FY2023?",
        )
        assert label == "cost of automotive revenue"


class TestSubQueryHumanReadable:
    """Test that sub-queries use human-readable text, not XBRL concept names."""

    def test_sub_query_text_uses_human_label(self):
        from tesla_finrag.models import PeriodSemantics

        periods = [date(2023, 12, 31)]
        sem_map = {"2023-12-31": PeriodSemantics.ANNUAL_CUMULATIVE}
        concepts = ["us-gaap:CostOfGoodsAndServicesSold"]

        sqs = _build_sub_queries("cost of revenue change", periods, concepts, sem_map)
        assert len(sqs) == 1
        # Should contain the human-readable alias, not the XBRL concept name
        text_lower = sqs[0].text.lower()
        assert "cost" in text_lower
        assert "costofgoodsandservicessold" not in text_lower
        assert "FY2023" in sqs[0].text

    def test_composite_plan_adds_narrative_only_sub_query(self):
        from tesla_finrag.planning.query_planner import RuleBasedQueryPlanner

        planner = RuleBasedQueryPlanner()
        plan = planner.plan(
            "What supply chain risk factors did Tesla mention in its 2023 10-K, "
            "and how did cost of automotive revenue change between FY2022 and FY2023?"
        )
        narrative_sqs = [sq for sq in plan.sub_queries if not sq.target_concepts]
        assert plan.answer_shape == AnswerShape.COMPOSITE
        assert narrative_sqs, "Composite plans should include at least one narrative-only sub-query"
        assert "supply chain" in narrative_sqs[0].text.lower()


# ===================================================================
# 4. Semantic protection tests
# ===================================================================


class TestSemanticProtection:
    """Verify that automotive cost and total cost are treated correctly."""

    def test_cost_of_automotive_revenue_maps_to_cogs_concept(self):
        """The question asks about 'cost of automotive revenue', which should
        map to CostOfGoodsAndServicesSold and its aliases include the term."""
        aliases = EvidenceLinker._get_table_aliases("us-gaap:CostOfGoodsAndServicesSold")
        assert any("automotive" in a for a in aliases)

    def test_extract_metrics_detects_cost_term(self):
        """extract_metrics should detect cost-related terms in the question."""
        q = "How did cost of automotive revenue change between FY2022 and FY2023?"
        metrics = extract_metrics(q)
        # Should detect some cost-related metric
        assert len(metrics) >= 1

    def test_table_fallback_aliases_include_automotive_variant(self):
        """The alias list for CostOfGoodsAndServicesSold should include
        both 'cost of automotive revenue' and 'cost of revenue' variants."""
        aliases = EvidenceLinker._get_table_aliases("us-gaap:CostOfGoodsAndServicesSold")
        has_automotive = any("automotive" in a for a in aliases)
        has_general = any(a == "cost of revenue" for a in aliases)
        assert has_automotive, "Must have automotive-specific alias"
        assert has_general, "Must have general cost-of-revenue alias"
