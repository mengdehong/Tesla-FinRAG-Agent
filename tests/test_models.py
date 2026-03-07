"""Tests for canonical typed domain models (tesla_finrag.models)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from tesla_finrag.models import (
    AnswerPayload,
    AnswerStatus,
    ChunkKind,
    Citation,
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    FilingType,
    QueryPlan,
    SectionChunk,
    TableChunk,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def annual_filing() -> FilingDocument:
    return FilingDocument(
        ticker="TSLA",
        filing_type=FilingType.ANNUAL,
        period_end=date(2023, 12, 31),
        fiscal_year=2023,
        fiscal_quarter=None,
        accession_number="0000950170-24-012345",
        filed_at=date(2024, 1, 29),
        source_path="data/raw/TSLA_10-K_2023.pdf",
    )


@pytest.fixture()
def quarterly_filing() -> FilingDocument:
    return FilingDocument(
        ticker="TSLA",
        filing_type=FilingType.QUARTERLY,
        period_end=date(2023, 9, 30),
        fiscal_year=2023,
        fiscal_quarter=3,
        accession_number="0000950170-23-099999",
        filed_at=date(2023, 10, 18),
        source_path="data/raw/TSLA_10-Q_Q3_2023.pdf",
    )


@pytest.fixture()
def section_chunk(annual_filing: FilingDocument) -> SectionChunk:
    return SectionChunk(
        doc_id=annual_filing.doc_id,
        section_title="Risk Factors",
        text="Our business is subject to numerous risks.",
        token_count=7,
        page_number=12,
    )


@pytest.fixture()
def table_chunk(annual_filing: FilingDocument) -> TableChunk:
    return TableChunk(
        doc_id=annual_filing.doc_id,
        section_title="Consolidated Statements of Operations",
        caption="Annual revenue table",
        headers=["Year", "Revenue (M)"],
        rows=[["2023", "96,773"], ["2022", "81,462"]],
        raw_text="Year | Revenue (M)\n2023 | 96,773\n2022 | 81,462",
    )


@pytest.fixture()
def fact_record(annual_filing: FilingDocument) -> FactRecord:
    return FactRecord(
        doc_id=annual_filing.doc_id,
        concept="us-gaap:Revenues",
        label="Total Revenues",
        value=96_773.0,
        unit="USD",
        scale=1_000_000,
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
    )


# ---------------------------------------------------------------------------
# FilingDocument tests
# ---------------------------------------------------------------------------


class TestFilingDocument:
    def test_auto_generates_doc_id(self, annual_filing: FilingDocument) -> None:
        assert isinstance(annual_filing.doc_id, UUID)

    def test_two_instances_have_different_ids(self) -> None:
        f1 = FilingDocument(
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            fiscal_year=2023,
            accession_number="A",
            filed_at=date(2024, 1, 1),
            source_path="data/raw/a.pdf",
        )
        f2 = FilingDocument(
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            fiscal_year=2023,
            accession_number="A",
            filed_at=date(2024, 1, 1),
            source_path="data/raw/a.pdf",
        )
        assert f1.doc_id != f2.doc_id

    def test_quarterly_requires_fiscal_quarter(self, quarterly_filing: FilingDocument) -> None:
        assert quarterly_filing.fiscal_quarter == 3

    def test_annual_fiscal_quarter_is_none(self, annual_filing: FilingDocument) -> None:
        assert annual_filing.fiscal_quarter is None

    def test_is_frozen(self, annual_filing: FilingDocument) -> None:
        with pytest.raises((TypeError, ValidationError)):
            annual_filing.ticker = "X"  # type: ignore[misc]

    def test_filing_type_enum(self, annual_filing: FilingDocument) -> None:
        assert annual_filing.filing_type == FilingType.ANNUAL
        assert annual_filing.filing_type.value == "10-K"


# ---------------------------------------------------------------------------
# Chunk tests
# ---------------------------------------------------------------------------


class TestSectionChunk:
    def test_kind_is_section(self, section_chunk: SectionChunk) -> None:
        assert section_chunk.kind == ChunkKind.SECTION

    def test_auto_generates_chunk_id(self, section_chunk: SectionChunk) -> None:
        assert isinstance(section_chunk.chunk_id, UUID)

    def test_negative_token_count_rejected(self, annual_filing: FilingDocument) -> None:
        with pytest.raises(ValidationError):
            SectionChunk(
                doc_id=annual_filing.doc_id,
                section_title="X",
                text="Y",
                token_count=-1,
            )


class TestTableChunk:
    def test_kind_is_table(self, table_chunk: TableChunk) -> None:
        assert table_chunk.kind == ChunkKind.TABLE

    def test_rows_preserved(self, table_chunk: TableChunk) -> None:
        assert table_chunk.rows[0] == ["2023", "96,773"]

    def test_default_empty_headers(self, annual_filing: FilingDocument) -> None:
        chunk = TableChunk(
            doc_id=annual_filing.doc_id,
            section_title="S",
            raw_text="raw",
        )
        assert chunk.headers == []


# ---------------------------------------------------------------------------
# FactRecord tests
# ---------------------------------------------------------------------------


class TestFactRecord:
    def test_value_stored(self, fact_record: FactRecord) -> None:
        assert fact_record.value == 96_773.0

    def test_unit_stored(self, fact_record: FactRecord) -> None:
        assert fact_record.unit == "USD"

    def test_scale_default_is_one(self, annual_filing: FilingDocument) -> None:
        f = FactRecord(
            doc_id=annual_filing.doc_id,
            concept="us-gaap:X",
            label="X",
            value=1.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        assert f.scale == 1

    def test_frozen(self, fact_record: FactRecord) -> None:
        with pytest.raises((TypeError, ValidationError)):
            fact_record.value = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QueryPlan tests
# ---------------------------------------------------------------------------


class TestQueryPlan:
    def test_default_fields(self) -> None:
        plan = QueryPlan(original_query="What was Tesla revenue in 2023?")
        assert plan.sub_questions == []
        assert plan.required_periods == []
        assert plan.needs_calculation is False
        assert isinstance(plan.created_at, datetime)

    def test_plan_id_is_uuid(self) -> None:
        plan = QueryPlan(original_query="Q")
        assert isinstance(plan.plan_id, UUID)


# ---------------------------------------------------------------------------
# EvidenceBundle tests
# ---------------------------------------------------------------------------


class TestEvidenceBundle:
    def test_bundle_links_to_plan(
        self,
        section_chunk: SectionChunk,
        table_chunk: TableChunk,
        fact_record: FactRecord,
    ) -> None:
        plan = QueryPlan(original_query="Q")
        bundle = EvidenceBundle(
            plan_id=plan.plan_id,
            section_chunks=[section_chunk],
            table_chunks=[table_chunk],
            facts=[fact_record],
        )
        assert bundle.plan_id == plan.plan_id
        assert len(bundle.section_chunks) == 1
        assert len(bundle.table_chunks) == 1
        assert len(bundle.facts) == 1

    def test_default_empty_collections(self) -> None:
        from uuid import uuid4

        bundle = EvidenceBundle(plan_id=uuid4())
        assert bundle.section_chunks == []
        assert bundle.facts == []


# ---------------------------------------------------------------------------
# AnswerPayload tests
# ---------------------------------------------------------------------------


class TestAnswerPayload:
    def test_ok_status(self, annual_filing: FilingDocument) -> None:
        plan = QueryPlan(original_query="Revenue?")
        citation = Citation(
            chunk_id=UUID(int=0),
            doc_id=annual_filing.doc_id,
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            excerpt="Total revenues were $96.8B.",
        )
        payload = AnswerPayload(
            plan_id=plan.plan_id,
            status=AnswerStatus.OK,
            answer_text="Tesla's 2023 revenue was $96.8 billion.",
            citations=[citation],
        )
        assert payload.status == AnswerStatus.OK
        assert payload.citations[0].excerpt == "Total revenues were $96.8B."

    def test_confidence_range_validated(self) -> None:
        from uuid import uuid4

        with pytest.raises(ValidationError):
            AnswerPayload(
                plan_id=uuid4(),
                status=AnswerStatus.OK,
                answer_text="Answer",
                confidence=1.5,  # out of [0, 1]
            )
