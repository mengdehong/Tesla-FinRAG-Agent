"""Ingestion pipeline tests.

Covers:
- Manifest building and gap detection
- Source adapter resolution and deterministic doc IDs
- Narrative section parsing metadata
- Table extraction metadata
- XBRL fact normalization and period_key alignment
- Processed data writers
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tesla_finrag.models import (
    ChunkKind,
    FactRecord,
    FilingAvailability,
    FilingDocument,
    FilingManifest,
    FilingType,
    ManifestEntry,
    SectionChunk,
    TableChunk,
)


def _section_signature(chunk: SectionChunk) -> tuple[object, ...]:
    return (
        chunk.page_number,
        chunk.char_offset,
        chunk.section_title,
        chunk.text,
        chunk.token_count,
    )


def _table_signature(chunk: TableChunk) -> tuple[object, ...]:
    return (
        chunk.page_number,
        chunk.section_title,
        chunk.caption,
        tuple(chunk.headers),
        tuple(tuple(row) for row in chunk.rows),
        chunk.raw_text,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    """Create a mock data/raw/ directory with a few filing PDFs."""
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    # Create empty placeholder files matching the naming convention.
    (raw / "Tesla_2023_全年_10-K.pdf").touch()
    (raw / "Tesla_2023_Q1_10-Q.pdf").touch()
    (raw / "Tesla_2023_Q2_10-Q.pdf").touch()
    # Intentionally missing: Q3 2023
    return raw


@pytest.fixture()
def companyfacts_path(tmp_path: Path) -> Path:
    """Create a minimal companyfacts.json fixture."""
    data = {
        "cik": 1318605,
        "entityName": "Tesla, Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "description": "Total revenues",
                    "units": {
                        "USD": [
                            {
                                "end": "2023-03-31",
                                "start": "2023-01-01",
                                "val": 23329000000,
                                "accn": "0001628280-23-012345",
                                "fy": 2023,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2023-04-24",
                            },
                            {
                                "end": "2023-12-31",
                                "start": "2023-01-01",
                                "val": 96773000000,
                                "accn": "0001628280-24-001234",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-29",
                            },
                            # Entry from a form we don't ingest.
                            {
                                "end": "2023-06-30",
                                "start": "2023-01-01",
                                "val": 48256000000,
                                "accn": "0001628280-23-099999",
                                "fy": 2023,
                                "fp": "Q2",
                                "form": "8-K",  # Should be skipped.
                                "filed": "2023-07-20",
                            },
                        ]
                    },
                },
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets",
                    "units": {
                        "USD": [
                            {
                                "end": "2023-12-31",
                                "val": 106618000000,
                                "accn": "0001628280-24-001234",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-29",
                            },
                        ]
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "label": "Net cash provided by operating activities",
                    "description": "Operating cash flow",
                    "units": {
                        "USD": [
                            {
                                "end": "2023-03-31",
                                "start": "2023-01-01",
                                "val": 2513000000,
                                "accn": "0001628280-23-012345",
                                "fy": 2023,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2023-04-24",
                            },
                            {
                                "end": "2023-12-31",
                                "start": "2023-01-01",
                                "val": 13256000000,
                                "accn": "0001628280-24-001234",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-29",
                            },
                        ]
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Payments to acquire property, plant and equipment",
                    "description": "Capital expenditures",
                    "units": {
                        "USD": [
                            {
                                "end": "2023-03-31",
                                "start": "2023-01-01",
                                "val": -2072000000,
                                "accn": "0001628280-23-012345",
                                "fy": 2023,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2023-04-24",
                            },
                            {
                                "end": "2023-12-31",
                                "start": "2023-01-01",
                                "val": -8898000000,
                                "accn": "0001628280-24-001234",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-29",
                            },
                        ]
                    },
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Entity Common Stock Shares Outstanding",
                    "units": {
                        "shares": [
                            {
                                "end": "2023-12-31",
                                "val": 3184786694,
                                "accn": "0001628280-24-001234",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-29",
                            }
                        ]
                    },
                }
            },
        },
    }
    path = tmp_path / "companyfacts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════
# 1. Manifest and source adapter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestManifest:
    """Tests for the filing manifest builder."""

    def test_build_manifest_enumerates_all_targets(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        # 1 annual + 3 quarterly = 4 targets per year.
        assert manifest.total == 4

    def test_build_manifest_detects_available_filings(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        available = manifest.available
        # We created 10-K, Q1, Q2 but not Q3.
        assert len(available) == 3
        types = {(e.filing_type, e.fiscal_quarter) for e in available}
        assert (FilingType.ANNUAL, None) in types
        assert (FilingType.QUARTERLY, 1) in types
        assert (FilingType.QUARTERLY, 2) in types

    def test_build_manifest_reports_gaps(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        gaps = manifest.gaps
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.fiscal_quarter == 3
        assert gap.status == FilingAvailability.DOWNLOADABLE

    def test_manifest_period_end_dates(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        period_ends = {(e.fiscal_quarter, e.period_end) for e in manifest.entries}
        assert (None, date(2023, 12, 31)) in period_ends  # 10-K
        assert (1, date(2023, 3, 31)) in period_ends  # Q1
        assert (2, date(2023, 6, 30)) in period_ends  # Q2
        assert (3, date(2023, 9, 30)) in period_ends  # Q3

    def test_manifest_summary_includes_gap_info(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest, print_manifest_summary

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        summary = print_manifest_summary(manifest)
        assert "Gaps:" in summary
        assert "Q3" in summary

    def test_scan_ignores_non_matching_files(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import scan_local_sources

        # Add a non-matching file.
        (raw_dir / "random_report.pdf").touch()
        inventory = scan_local_sources(raw_dir)
        # Should not include the random file.
        assert all("random" not in v for v in inventory.values())


class TestSourceAdapter:
    """Tests for the source adapter and document resolution."""

    def test_resolve_filing_document_returns_none_for_missing(self) -> None:
        from tesla_finrag.ingestion.source_adapter import resolve_filing_document

        entry = ManifestEntry(
            filing_type=FilingType.QUARTERLY,
            fiscal_year=2023,
            fiscal_quarter=3,
            period_end=date(2023, 9, 30),
            status=FilingAvailability.DOWNLOADABLE,
        )
        assert resolve_filing_document(entry) is None

    def test_resolve_filing_document_returns_doc_for_available(self) -> None:
        from tesla_finrag.ingestion.source_adapter import resolve_filing_document

        entry = ManifestEntry(
            filing_type=FilingType.ANNUAL,
            fiscal_year=2023,
            fiscal_quarter=None,
            period_end=date(2023, 12, 31),
            status=FilingAvailability.AVAILABLE,
            source_path="data/raw/Tesla_2023_全年_10-K.pdf",
        )
        doc = resolve_filing_document(entry)
        assert doc is not None
        assert doc.filing_type == FilingType.ANNUAL
        assert doc.fiscal_year == 2023
        assert doc.period_end == date(2023, 12, 31)
        assert doc.source_path == "data/raw/Tesla_2023_全年_10-K.pdf"

    def test_deterministic_doc_ids(self) -> None:
        from tesla_finrag.ingestion.source_adapter import resolve_filing_document

        entry = ManifestEntry(
            filing_type=FilingType.ANNUAL,
            fiscal_year=2023,
            fiscal_quarter=None,
            period_end=date(2023, 12, 31),
            status=FilingAvailability.AVAILABLE,
            source_path="data/raw/Tesla_2023_全年_10-K.pdf",
        )
        doc1 = resolve_filing_document(entry)
        doc2 = resolve_filing_document(entry)
        assert doc1 is not None and doc2 is not None
        assert doc1.doc_id == doc2.doc_id

    def test_period_key_formatting(self) -> None:
        from tesla_finrag.ingestion.source_adapter import period_key

        assert period_key(2023, None) == "FY2023"
        assert period_key(2023, 1) == "Q1-2023"
        assert period_key(2023, 3) == "Q3-2023"

    def test_resolve_all_skips_unavailable(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest
        from tesla_finrag.ingestion.source_adapter import resolve_all_filings

        manifest = build_manifest(raw_dir, years=range(2023, 2024))
        filings = resolve_all_filings(manifest)
        # 3 available files, not 4.
        assert len(filings) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 2. Narrative parsing tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestNarrativeParsing:
    """Tests for section-aware narrative chunk extraction."""

    def test_parse_narrative_returns_chunks(self) -> None:
        """Smoke test against an actual filing if available."""
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.narrative import parse_narrative

        doc_id = uuid4()
        chunks = parse_narrative(pdf_path, doc_id)
        assert len(chunks) > 0
        assert all(isinstance(c, SectionChunk) for c in chunks)

    def test_chunks_have_provenance_metadata(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.narrative import parse_narrative

        doc_id = uuid4()
        chunks = parse_narrative(pdf_path, doc_id)
        for chunk in chunks:
            assert chunk.doc_id == doc_id
            assert chunk.kind == ChunkKind.SECTION
            assert chunk.page_number is not None and chunk.page_number >= 1
            assert chunk.section_title  # Non-empty section title.
            assert chunk.text  # Non-empty text.
            assert chunk.token_count > 0

    def test_section_titles_are_detected(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.narrative import parse_narrative

        doc_id = uuid4()
        chunks = parse_narrative(pdf_path, doc_id)
        section_titles = {c.section_title for c in chunks}
        # 10-Q should have at least Item 1 and Item 2 (MD&A).
        has_item_1 = any("Item 1" in t for t in section_titles)
        has_item_2 = any("Item 2" in t for t in section_titles)
        assert has_item_1, f"Expected Item 1 in sections: {section_titles}"
        assert has_item_2, f"Expected Item 2 in sections: {section_titles}"

    def test_chunk_token_count_within_bounds(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.narrative import parse_narrative

        doc_id = uuid4()
        chunks = parse_narrative(pdf_path, doc_id, max_chunk_tokens=800)
        for chunk in chunks:
            # Allow some tolerance since chunking is approximate.
            assert chunk.token_count <= 1000, (
                f"Chunk too large: {chunk.token_count} tokens in {chunk.section_title}"
            )

    def test_toc_page_detection(self) -> None:
        from tesla_finrag.ingestion.narrative import _is_toc_page

        # A page with many ITEM headers is a TOC.
        toc_text = "\n".join(
            [
                "PART I. FINANCIAL INFORMATION",
                "Item 1. Financial Statements 4",
                "Item 2. Management's Discussion 23",
                "Item 3. Quantitative Disclosures 31",
                "Item 4. Controls 31",
                "PART II. OTHER INFORMATION",
                "Item 1. Legal Proceedings 32",
            ]
        )
        assert _is_toc_page(toc_text) is True

        # A normal content page with at most one header.
        content_text = "Item 2. MANAGEMENT'S DISCUSSION\nSome analysis text here..."
        assert _is_toc_page(content_text) is False

    def test_detect_sections_splits_multiple_headers_on_same_page(self) -> None:
        from tesla_finrag.ingestion.narrative import _detect_sections

        pages = [
            (
                5,
                "Item 1. Financial Statements\nBalance sheet text\n"
                "Item 2. Management's Discussion\nMD&A text",
            )
        ]

        sections = _detect_sections(pages)
        assert [title for title, _, _ in sections] == [
            "Item 1. Financial Statements",
            "Item 2. Management's Discussion",
        ]
        assert "Balance sheet text" in sections[0][2]
        assert "MD&A text" in sections[1][2]

    def test_chunk_text_uses_overlap_and_respects_size(self) -> None:
        from tesla_finrag.ingestion.narrative import _chunk_text

        text = "Paragraph one. " * 40 + "\n\n" + "Paragraph two. " * 40
        chunks = _chunk_text(text, max_tokens=20, overlap_tokens=5)

        assert len(chunks) > 1
        assert chunks[0][1] == 0
        assert chunks[1][1] > 0
        assert all(chunk_text.strip() for chunk_text, _ in chunks)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Table extraction tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestTableExtraction:
    """Tests for table extraction and normalization."""

    def test_extract_tables_returns_chunks(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.tables import extract_tables

        doc_id = uuid4()
        chunks = extract_tables(pdf_path, doc_id)
        assert len(chunks) > 0
        assert all(isinstance(c, TableChunk) for c in chunks)

    def test_table_chunks_have_structured_metadata(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.tables import extract_tables

        doc_id = uuid4()
        chunks = extract_tables(pdf_path, doc_id)
        for chunk in chunks:
            assert chunk.doc_id == doc_id
            assert chunk.kind == ChunkKind.TABLE
            assert chunk.page_number is not None and chunk.page_number >= 1
            assert chunk.section_title  # Has section context.
            assert chunk.headers  # Has column headers.
            assert chunk.rows  # Has data rows.
            assert chunk.raw_text  # Has serialized text.

    def test_table_has_section_context(self) -> None:
        pdf_path = Path("data/raw/Tesla_2023_Q1_10-Q.pdf")
        if not pdf_path.exists():
            pytest.skip("Test data not available")
        from tesla_finrag.ingestion.tables import extract_tables

        doc_id = uuid4()
        chunks = extract_tables(pdf_path, doc_id)
        # Financial statement tables should be in Item 1.
        financial_tables = [c for c in chunks if "Item 1" in c.section_title]
        assert len(financial_tables) > 0, "Expected tables under Item 1"

    def test_table_cleaning_handles_none_cells(self) -> None:
        from tesla_finrag.ingestion.tables import _clean_table

        raw = [["Header", None, "Value"], ["Row1", None, "100"], [None, None, None]]
        headers, rows = _clean_table(raw)
        assert headers == ["Header", "", "Value"]
        # The all-None row should be dropped.
        assert len(rows) == 1

    def test_table_to_text_preserves_empty_cells(self) -> None:
        from tesla_finrag.ingestion.tables import _table_to_text

        text = _table_to_text(["Year", "Revenue", "Notes"], [["2023", "96773", ""]])

        assert text.splitlines()[0] == "Year | Revenue | Notes"
        assert text.splitlines()[1] == "2023 | 96773 | "

    def test_extract_caption_uses_table_index(self) -> None:
        from tesla_finrag.ingestion.tables import _extract_caption

        page_text = "\n".join(
            [
                "Consolidated Balance Sheets",
                "Some spacing",
                "Consolidated Statements of Operations",
            ]
        )

        assert _extract_caption(page_text, 0) == "Consolidated Balance Sheets"
        assert _extract_caption(page_text, 1) == "Consolidated Statements of Operations"


# ═══════════════════════════════════════════════════════════════════════════
# 3.1 Shared PDF analysis regression tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestSharedPdfAnalysis:
    def _legacy_narrative(self, pdf_path: Path, doc_id) -> list[SectionChunk]:
        import pdfplumber

        from tesla_finrag.ingestion.narrative import (
            _chunk_text,
            _detect_sections,
            _estimate_tokens,
        )

        pages: list[tuple[int, str]] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append((page_num, text))

        chunks: list[SectionChunk] = []
        for section_title, start_page, section_text in _detect_sections(pages):
            for chunk_text, char_offset in _chunk_text(section_text):
                if not chunk_text.strip():
                    continue
                chunks.append(
                    SectionChunk(
                        doc_id=doc_id,
                        kind=ChunkKind.SECTION,
                        page_number=start_page,
                        char_offset=char_offset,
                        section_title=section_title,
                        text=chunk_text,
                        token_count=_estimate_tokens(chunk_text),
                    )
                )
        return chunks

    def _legacy_tables(self, pdf_path: Path, doc_id) -> list[TableChunk]:
        import pdfplumber

        from tesla_finrag.ingestion.tables import (
            _clean_table,
            _current_section_from_page,
            _extract_caption,
            _table_to_text,
        )

        chunks: list[TableChunk] = []
        current_section = "Unknown"
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                current_section = _current_section_from_page(page_text, current_section)
                for table_idx, raw_table in enumerate(page.extract_tables() or []):
                    if not raw_table:
                        continue
                    headers, rows = _clean_table(raw_table)
                    if len(rows) < 2:
                        continue
                    raw_text = _table_to_text(headers, rows)
                    if not raw_text.strip():
                        continue
                    chunks.append(
                        TableChunk(
                            doc_id=doc_id,
                            kind=ChunkKind.TABLE,
                            page_number=page_num,
                            section_title=current_section,
                            caption=_extract_caption(page_text, table_idx),
                            headers=headers,
                            rows=rows,
                            raw_text=raw_text,
                        )
                    )
        return chunks

    @pytest.mark.parametrize(
        "filename",
        ["Tesla_2023_Q1_10-Q.pdf", "Tesla_2023_全年_10-K.pdf"],
    )
    def test_shared_analysis_preserves_narrative_output(self, filename: str) -> None:
        pdf_path = Path("data/raw") / filename
        if not pdf_path.exists():
            pytest.skip("Test data not available")

        from tesla_finrag.ingestion.analysis import analyze_filing_pdf
        from tesla_finrag.ingestion.narrative import narrative_chunks_from_analysis

        doc_id = uuid4()
        analysis = analyze_filing_pdf(pdf_path)

        legacy = self._legacy_narrative(pdf_path, doc_id)
        shared = narrative_chunks_from_analysis(analysis, doc_id)

        assert [_section_signature(chunk) for chunk in shared] == [
            _section_signature(chunk) for chunk in legacy
        ]

    @pytest.mark.parametrize(
        "filename",
        ["Tesla_2023_Q1_10-Q.pdf", "Tesla_2023_全年_10-K.pdf"],
    )
    def test_shared_analysis_preserves_table_output(self, filename: str) -> None:
        pdf_path = Path("data/raw") / filename
        if not pdf_path.exists():
            pytest.skip("Test data not available")

        from tesla_finrag.ingestion.analysis import analyze_filing_pdf
        from tesla_finrag.ingestion.tables import table_chunks_from_analysis

        doc_id = uuid4()
        analysis = analyze_filing_pdf(pdf_path)

        legacy = self._legacy_tables(pdf_path, doc_id)
        shared = table_chunks_from_analysis(analysis, doc_id)

        assert [_table_signature(chunk) for chunk in shared] == [
            _table_signature(chunk) for chunk in legacy
        ]


# ═══════════════════════════════════════════════════════════════════════════
# 4. XBRL normalization tests
# ═══════════════════════════════════════════════════════════════════════════


class TestXBRLNormalization:
    """Tests for companyfacts/XBRL fact normalization."""

    def test_normalize_produces_fact_records(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        assert len(records) > 0
        assert all(isinstance(r, FactRecord) for r in records)

    def test_skips_non_accepted_forms(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        # The 8-K entry should be skipped; only 10-K and 10-Q accepted.
        # We have 2 Revenue + 1 Assets + 2 OCF + 2 PP&E + 1 dei + 2 derived capex
        # + 2 derived free cash flow = 12.
        assert len(records) == 12

    def test_duration_vs_instant_facts(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        revenue_facts = [r for r in records if "Revenues" in r.concept]
        asset_facts = [r for r in records if "Assets" in r.concept]

        # Revenue is a duration fact (has start and end).
        for r in revenue_facts:
            assert r.is_instant is False
            assert r.period_start is not None

        # Assets is an instant fact (balance sheet, no start).
        for r in asset_facts:
            assert r.is_instant is True
            assert r.period_start is None

    def test_period_end_dates_correct(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        revenue_facts = [r for r in records if "Revenues" in r.concept]
        period_ends = {r.period_end for r in revenue_facts}
        assert date(2023, 3, 31) in period_ends  # Q1
        assert date(2023, 12, 31) in period_ends  # FY

    def test_concept_names_include_namespace(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        concepts = {r.concept for r in records}
        assert "us-gaap:Revenues" in concepts
        assert "us-gaap:Assets" in concepts
        assert "dei:EntityCommonStockSharesOutstanding" in concepts

    def test_doc_id_matches_source_adapter(self, companyfacts_path: Path) -> None:
        """Fact doc_ids should match the deterministic IDs from source_adapter."""
        from tesla_finrag.ingestion.source_adapter import _stable_doc_id
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        # The FY2023 10-K fact should match a doc_id for TSLA/10-K/2023/FY.
        fy_facts = [
            r
            for r in records
            if r.concept == "us-gaap:Revenues" and r.period_end == date(2023, 12, 31)
        ]
        assert len(fy_facts) >= 1
        expected_id = _stable_doc_id("TSLA", "10-K", 2023, None)
        assert fy_facts[0].doc_id == expected_id

    def test_units_preserved(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        units = {r.unit for r in records}
        assert "USD" in units
        assert "shares" in units

    def test_derives_custom_capex_and_free_cash_flow(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        concepts = {r.concept for r in records}
        assert "custom:CapitalExpenditure" in concepts
        assert "custom:FreeCashFlow" in concepts

        fy_capex = next(
            r
            for r in records
            if r.concept == "custom:CapitalExpenditure" and r.period_end == date(2023, 12, 31)
        )
        fy_fcf = next(
            r
            for r in records
            if r.concept == "custom:FreeCashFlow" and r.period_end == date(2023, 12, 31)
        )

        assert fy_capex.value == 8_898_000_000
        assert fy_fcf.value == 4_358_000_000
        assert fy_capex.period_start == date(2023, 1, 1)
        assert fy_fcf.period_start == date(2023, 1, 1)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Writer tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWriters:
    """Tests for processed data writers."""

    def test_write_manifest_creates_file(self) -> None:
        from tesla_finrag.ingestion.writers import write_manifest

        manifest = FilingManifest(
            entries=[
                ManifestEntry(
                    filing_type=FilingType.ANNUAL,
                    fiscal_year=2023,
                    period_end=date(2023, 12, 31),
                    status=FilingAvailability.AVAILABLE,
                    source_path="data/raw/test.pdf",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            path = write_manifest(manifest, Path(td))
            assert path.exists()
            data = json.loads(path.read_text())
            assert len(data["entries"]) == 1

    def test_write_filings_creates_json_per_doc(self) -> None:
        from tesla_finrag.ingestion.writers import write_filings

        filings = [
            FilingDocument(
                filing_type=FilingType.ANNUAL,
                period_end=date(2023, 12, 31),
                fiscal_year=2023,
                accession_number="test-accn",
                filed_at=date(2024, 2, 1),
                source_path="data/raw/test.pdf",
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            paths = write_filings(filings, Path(td))
            assert len(paths) == 1
            assert paths[0].exists()

    def test_write_facts_creates_jsonl(self, companyfacts_path: Path) -> None:
        from tesla_finrag.ingestion.writers import write_facts
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts_path)
        with tempfile.TemporaryDirectory() as td:
            path = write_facts(records, Path(td))
            assert path.exists()
            lines = path.read_text().strip().split("\n")
            assert len(lines) == len(records)
            # Each line should be valid JSON.
            for line in lines:
                data = json.loads(line)
                assert "concept" in data

    def test_write_section_chunks(self) -> None:
        from tesla_finrag.ingestion.writers import write_section_chunks

        doc_id = uuid4()
        chunks = [
            SectionChunk(
                doc_id=doc_id,
                section_title="Item 7. MD&A",
                text="Test content",
                token_count=2,
                page_number=5,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            paths = write_section_chunks(chunks, doc_id, Path(td))
            assert len(paths) == 1
            assert paths[0].exists()
            data = json.loads(paths[0].read_text())
            assert data["section_title"] == "Item 7. MD&A"

    def test_write_table_chunks(self) -> None:
        from tesla_finrag.ingestion.writers import write_table_chunks

        doc_id = uuid4()
        chunks = [
            TableChunk(
                doc_id=doc_id,
                section_title="Item 8. Financial Statements",
                headers=["Year", "Revenue"],
                rows=[["2023", "96773"]],
                raw_text="Year | Revenue\n2023 | 96773",
                page_number=50,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            paths = write_table_chunks(chunks, doc_id, Path(td))
            assert len(paths) == 1
            data = json.loads(paths[0].read_text())
            assert data["headers"] == ["Year", "Revenue"]

    def test_outputs_are_outside_raw(self) -> None:
        from tesla_finrag.ingestion.writers import _DEFAULT_OUTPUT_DIR

        assert "processed" in str(_DEFAULT_OUTPUT_DIR)
        assert "raw" not in str(_DEFAULT_OUTPUT_DIR)


class TestPipeline:
    @staticmethod
    def _fake_chunks(
        filing: FilingDocument,
    ) -> tuple[list[SectionChunk], list[TableChunk]]:
        period_label = "FY" if filing.fiscal_quarter is None else f"Q{filing.fiscal_quarter}"
        sections = [
            SectionChunk(
                doc_id=filing.doc_id,
                section_title=period_label,
                text=f"Narrative for {period_label}-{filing.fiscal_year}",
                token_count=4,
                page_number=1,
            )
        ]
        tables = [
            TableChunk(
                doc_id=filing.doc_id,
                section_title="Item 1. Financial Statements",
                caption=f"Revenue {filing.fiscal_year}",
                headers=["Metric", "Value"],
                rows=[["Revenue", "100"]],
                raw_text="Metric | Value\nRevenue | 100",
                page_number=2,
            )
        ]
        return sections, tables

    def test_resolve_source_pdf_path_handles_repo_relative_sources(self, raw_dir: Path) -> None:
        from tesla_finrag.ingestion.pipeline import _resolve_source_pdf_path

        path = _resolve_source_pdf_path(raw_dir, "data/raw/Tesla_2023_Q1_10-Q.pdf")

        assert path == raw_dir / "Tesla_2023_Q1_10-Q.pdf"

    def test_run_pipeline_continues_after_parse_failure(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def boom(*args: object, **kwargs: object) -> list[SectionChunk]:
            raise ValueError("broken pdf")

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.analyze_filing_pdf",
            lambda *args, **kwargs: object(),
        )
        monkeypatch.setattr("tesla_finrag.ingestion.pipeline.narrative_chunks_from_analysis", boom)
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.table_chunks_from_analysis",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        summary = run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed")

        assert summary["filings"] == 3
        assert summary["section_chunks"] == 0
        assert summary["failed_filings"] == 3
        assert len(summary["failed_details"]) == 3

    def test_run_pipeline_falls_back_to_sequential_when_parallel_unavailable(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        calls: list[int] = []

        _empty_diag = {
            "fallback_pages": 0,
            "failed_pages": 0,
            "validation_failed_tables": 0,
            "validation_suspect_tables": 0,
        }

        def fake_run_jobs(
            filings: list[object],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            calls.append(workers)
            if workers > 1:
                raise PermissionError("sandbox blocked multiprocessing")
            return {}, {}, [], _empty_diag.copy()

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        summary = run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed", workers=4)

        assert calls == [4, 1]
        assert summary["workers"] == 1

    def test_run_pipeline_reuses_unchanged_filings_on_rerun(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        calls: list[list[str]] = []

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            calls.append([filing.source_path for filing in filings])
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr("tesla_finrag.ingestion.pipeline.os.cpu_count", lambda: 8)
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        first = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        second = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)

        assert first["reprocessed_filings"] == 3
        assert first["reused_filings"] == 0
        assert first["workers"] == 3
        assert second["reprocessed_filings"] == 0
        assert second["reused_filings"] == 3
        assert second["workers"] == 1
        assert calls == [
            [
                "data/raw/Tesla_2023_全年_10-K.pdf",
                "data/raw/Tesla_2023_Q1_10-Q.pdf",
                "data/raw/Tesla_2023_Q2_10-Q.pdf",
            ],
            [],
        ]

    def test_run_pipeline_reprocesses_only_changed_filing(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        calls: list[list[str]] = []

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            calls.append([Path(filing.source_path).name for filing in filings])
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr("tesla_finrag.ingestion.pipeline.os.cpu_count", lambda: 8)
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        (raw_dir / "Tesla_2023_Q1_10-Q.pdf").write_text("changed", encoding="utf-8")

        summary = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)

        assert summary["reprocessed_filings"] == 1
        assert summary["reused_filings"] == 2
        assert summary["workers"] == 1
        assert calls[-1] == ["Tesla_2023_Q1_10-Q.pdf"]

    def test_run_pipeline_reuses_and_invalidates_companyfacts(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        companyfacts = raw_dir / "companyfacts.json"
        companyfacts.write_text('{"facts":{}}', encoding="utf-8")
        normalize_calls: list[str] = []

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        def fake_normalize(path: Path) -> list[FactRecord]:
            normalize_calls.append(path.read_text(encoding="utf-8"))
            return [
                FactRecord(
                    doc_id=uuid4(),
                    concept="us-gaap:Revenues",
                    label="Revenue",
                    value=1.0,
                    unit="USD",
                    period_end=date(2023, 3, 31),
                )
            ]

        monkeypatch.setattr("tesla_finrag.ingestion.pipeline.os.cpu_count", lambda: 8)
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.normalize_companyfacts",
            fake_normalize,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.summarize_facts",
            lambda records: f"{len(records)} facts",
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        first = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        second = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        companyfacts.write_text('{"facts":{"updated":true}}', encoding="utf-8")
        third = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)

        assert first["facts_reused"] is False
        assert second["facts_reused"] is True
        assert third["facts_reused"] is False
        assert len(normalize_calls) == 2

    def test_run_pipeline_reprocesses_when_state_file_is_missing(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline
        from tesla_finrag.ingestion.state import state_path_for

        calls: list[int] = []

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            calls.append(len(filings))
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr("tesla_finrag.ingestion.pipeline.os.cpu_count", lambda: 8)
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        state_path_for(output_dir).unlink()

        summary = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)

        assert summary["reprocessed_filings"] == 3
        assert summary["reused_filings"] == 0
        assert calls == [3, 3]

    def test_run_pipeline_raises_when_lancedb_build_fails(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("index failed")),
        )

        with pytest.raises(RuntimeError, match="index failed"):
            run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed")

    def test_build_lancedb_index_deletes_removed_doc_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import _build_lancedb_index
        from tesla_finrag.ingestion.state import FilingStateEntry, IngestionState
        from tesla_finrag.ingestion.writers import remove_filing_artifacts, write_filing_bundle
        from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore

        output_dir = tmp_path / "processed"
        filing_a = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2023, 3, 31),
            fiscal_year=2023,
            fiscal_quarter=1,
            accession_number="0000950170-2023-03",
            filed_at=date(2023, 4, 15),
            source_path="data/raw/Tesla_2023_Q1_10-Q.pdf",
        )
        filing_b = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2023, 6, 30),
            fiscal_year=2023,
            fiscal_quarter=2,
            accession_number="0000950170-2023-06",
            filed_at=date(2023, 7, 15),
            source_path="data/raw/Tesla_2023_Q2_10-Q.pdf",
        )
        sections_a, tables_a = self._fake_chunks(filing_a)
        sections_b, tables_b = self._fake_chunks(filing_b)
        write_filing_bundle(filing_a, sections_a, tables_a, output_dir)
        write_filing_bundle(filing_b, sections_b, tables_b, output_dir)

        store = LanceDBRetrievalStore(output_dir / "lancedb")
        for chunk in [*sections_a, *tables_a, *sections_b, *tables_b]:
            if isinstance(chunk, SectionChunk):
                store.index_section_chunk(chunk, [0.1, 0.2, 0.3])
            else:
                store.index_table_chunk(chunk, [0.1, 0.2, 0.3])
        store.save_metadata(
            {
                "index_schema_version": 2,
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": 3,
                "source_chunk_count": (
                    len(sections_a) + len(tables_a) + len(sections_b) + len(tables_b)
                ),
                "vector_row_count": store.chunk_count,
                "chunk_count": store.chunk_count,
            }
        )

        class FakeIndexingProvider:
            embedding_model = "nomic-embed-text"
            base_url = "http://localhost:11434/v1"
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[0.1, 0.2, 0.3] for _ in texts]

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.IndexingEmbeddingProvider.from_settings",
            lambda: FakeIndexingProvider(),
        )

        remove_filing_artifacts(filing_b.doc_id, output_dir)
        state = IngestionState(
            filings={
                str(filing_a.doc_id): FilingStateEntry(
                    doc_id=filing_a.doc_id,
                    source_path=filing_a.source_path,
                    source_fingerprint="fingerprint-a",
                    parser_fingerprint="parser",
                    section_chunk_count=len(sections_a),
                    table_chunk_count=len(tables_a),
                )
            }
        )

        indexed_count = _build_lancedb_index(
            output_dir,
            state,
            refreshed_doc_ids=set(),
            removed_doc_ids={filing_b.doc_id},
        )

        reloaded_store = LanceDBRetrievalStore(output_dir / "lancedb")
        assert indexed_count == len(sections_a) + len(tables_a)
        assert reloaded_store.chunk_count == indexed_count
        metadata = reloaded_store.load_metadata()
        assert metadata is not None
        assert metadata["index_schema_version"] == 2
        assert metadata["source_chunk_count"] == len(sections_a) + len(tables_a)
        assert metadata["vector_row_count"] == indexed_count

    def test_segment_chunk_for_indexing_splits_oversized_narrative(self) -> None:
        from tesla_finrag.ingestion.index_segmentation import segment_chunk_for_indexing

        doc_id = uuid4()
        long_text = ("\n\n".join([("Sentence. " * 120) for _ in range(4)])).strip()
        chunk = SectionChunk(
            doc_id=doc_id,
            section_title="MD&A",
            text=long_text,
            token_count=1200,
        )

        segments = segment_chunk_for_indexing(chunk, max_chars=500, overlap_chars=50)
        assert len(segments) > 1
        assert [segment.segment_index for segment in segments] == list(range(len(segments)))
        assert all(segment.segment_count == len(segments) for segment in segments)
        assert all(len(segment.text) <= 500 for segment in segments)

    def test_segment_chunk_for_indexing_splits_oversized_table_with_header_context(self) -> None:
        from tesla_finrag.ingestion.index_segmentation import segment_chunk_for_indexing

        doc_id = uuid4()
        table_lines = ["Header A | Header B"] + [f"row-{i} | value-{i}" for i in range(120)]
        raw_text = "\n".join(table_lines)
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Financial Statements",
            headers=["Header A", "Header B"],
            rows=[[f"row-{i}", f"value-{i}"] for i in range(120)],
            raw_text=raw_text,
        )

        segments = segment_chunk_for_indexing(chunk, max_chars=280, overlap_chars=40)
        assert len(segments) > 1
        assert all(len(segment.text) <= 280 for segment in segments)
        assert all(segment.text.startswith("Header A | Header B") for segment in segments[1:])

    def test_segment_chunk_for_indexing_preserves_single_line_table_content(self) -> None:
        from tesla_finrag.ingestion.index_segmentation import segment_chunk_for_indexing

        doc_id = uuid4()
        tail = "".join(f"{i:04d}" for i in range(400))
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Financial Statements",
            headers=["H1"],
            rows=[["row"]],
            raw_text=f"Header\nrow | {tail}",
        )

        segments = segment_chunk_for_indexing(chunk, max_chars=280, overlap_chars=40)
        combined = "\n".join(segment.text for segment in segments)

        assert len(segments) > 1
        assert all(len(segment.text) <= 280 for segment in segments)
        assert tail[:80] in combined
        assert tail[600:680] in combined
        assert tail[-80:] in combined

    def test_build_lancedb_index_segments_oversized_chunk_for_local_limits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import _build_lancedb_index
        from tesla_finrag.ingestion.state import FilingStateEntry, IngestionState
        from tesla_finrag.ingestion.writers import write_filing_bundle
        from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore

        filing = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2024, 3, 31),
            fiscal_year=2024,
            fiscal_quarter=1,
            accession_number="0000950170-2024-03",
            filed_at=date(2024, 4, 15),
            source_path="data/raw/Tesla_2024_Q1_10-Q.pdf",
        )
        oversized_table = TableChunk(
            doc_id=filing.doc_id,
            section_title="Financial Statements",
            caption="Oversized table",
            headers=["Metric", "Value"],
            rows=[[f"Metric {i}", f"{i}"] for i in range(300)],
            raw_text="\n".join(["Metric | Value"] + [f"Metric {i} | {i}" for i in range(300)]),
        )
        output_dir = tmp_path / "processed"
        write_filing_bundle(filing, [], [oversized_table], output_dir)

        class FakeOllamaProvider:
            embedding_model = "nomic-embed-text"
            base_url = "http://localhost:11434/v1"
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                for text in texts:
                    if len(text) > 2400:
                        raise RuntimeError("400 input length exceeds the context length")
                return [[0.1, 0.2, 0.3] for _ in texts]

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.IndexingEmbeddingProvider.from_settings",
            lambda: FakeOllamaProvider(),
        )

        state = IngestionState(
            filings={
                str(filing.doc_id): FilingStateEntry(
                    doc_id=filing.doc_id,
                    source_path=filing.source_path,
                    source_fingerprint="fingerprint",
                    parser_fingerprint="parser",
                    section_chunk_count=0,
                    table_chunk_count=1,
                )
            }
        )

        row_count = _build_lancedb_index(
            output_dir,
            state,
            refreshed_doc_ids={filing.doc_id},
            removed_doc_ids=set(),
        )

        store = LanceDBRetrievalStore(output_dir / "lancedb")
        metadata = store.load_metadata()
        assert row_count > 1
        assert metadata is not None
        assert metadata["source_chunk_count"] == 1
        assert metadata["vector_row_count"] == row_count

    def test_build_lancedb_index_batches_segments_globally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import _build_lancedb_index
        from tesla_finrag.ingestion.state import FilingStateEntry, IngestionState
        from tesla_finrag.ingestion.writers import write_filing_bundle

        filing = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2024, 9, 30),
            fiscal_year=2024,
            fiscal_quarter=3,
            accession_number="0000950170-2024-09",
            filed_at=date(2024, 10, 15),
            source_path="data/raw/Tesla_2024_Q3_10-Q.pdf",
        )
        sections = [
            SectionChunk(
                doc_id=filing.doc_id,
                section_title=f"Section {index}",
                text=f"short text {index}",
                token_count=3,
            )
            for index in range(70)
        ]
        output_dir = tmp_path / "processed"
        write_filing_bundle(filing, sections, [], output_dir)

        class BatchTrackingProvider:
            embedding_model = "nomic-embed-text"
            base_url = "http://localhost:11434/v1"
            batch_sizes: list[int] = []
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                self.batch_sizes.append(len(texts))
                return [[0.1, 0.2, 0.3] for _ in texts]

        provider = BatchTrackingProvider()
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.IndexingEmbeddingProvider.from_settings",
            lambda: provider,
        )

        state = IngestionState(
            filings={
                str(filing.doc_id): FilingStateEntry(
                    doc_id=filing.doc_id,
                    source_path=filing.source_path,
                    source_fingerprint="fingerprint",
                    parser_fingerprint="parser",
                    section_chunk_count=len(sections),
                    table_chunk_count=0,
                )
            }
        )

        row_count = _build_lancedb_index(
            output_dir,
            state,
            refreshed_doc_ids={filing.doc_id},
            removed_doc_ids=set(),
        )

        assert row_count == len(sections)
        assert provider.batch_sizes == [64, 6]

    def test_build_lancedb_index_writes_rows_in_batches_without_per_chunk_upserts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import _build_lancedb_index
        from tesla_finrag.ingestion.state import FilingStateEntry, IngestionState
        from tesla_finrag.ingestion.writers import write_filing_bundle
        from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore

        filing = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2025, 3, 31),
            fiscal_year=2025,
            fiscal_quarter=1,
            accession_number="0000950170-2025-03",
            filed_at=date(2025, 4, 15),
            source_path="data/raw/Tesla_2025_Q1_10-Q.pdf",
        )
        sections = [
            SectionChunk(
                doc_id=filing.doc_id,
                section_title=f"Section {index}",
                text=f"short text {index}",
                token_count=3,
            )
            for index in range(10)
        ]
        output_dir = tmp_path / "processed"
        write_filing_bundle(filing, sections, [], output_dir)

        class BatchTrackingProvider:
            embedding_model = "nomic-embed-text"
            base_url = "http://localhost:11434/v1"
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[0.1, 0.2, 0.3] for _ in texts]

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.IndexingEmbeddingProvider.from_settings",
            lambda: BatchTrackingProvider(),
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._INDEX_WRITE_BATCH_SIZE",
            4,
        )

        batch_sizes: list[int] = []
        original_add_rows = LanceDBRetrievalStore.add_rows

        def tracking_add_rows(self, rows: list[dict[str, object]]) -> None:
            batch_sizes.append(len(rows))
            original_add_rows(self, rows)

        def fail_index_chunk_segments(*args, **kwargs) -> None:
            raise AssertionError("per-chunk upserts should not be used during batched indexing")

        monkeypatch.setattr(LanceDBRetrievalStore, "add_rows", tracking_add_rows)
        monkeypatch.setattr(
            LanceDBRetrievalStore,
            "index_chunk_segments",
            fail_index_chunk_segments,
        )

        state = IngestionState(
            filings={
                str(filing.doc_id): FilingStateEntry(
                    doc_id=filing.doc_id,
                    source_path=filing.source_path,
                    source_fingerprint="fingerprint",
                    parser_fingerprint="parser",
                    section_chunk_count=len(sections),
                    table_chunk_count=0,
                )
            }
        )

        row_count = _build_lancedb_index(
            output_dir,
            state,
            refreshed_doc_ids={filing.doc_id},
            removed_doc_ids=set(),
        )

        assert row_count == len(sections)
        assert batch_sizes == [4, 4, 2]

    def test_build_lancedb_index_reports_unindexable_chunk_diagnostics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import _build_lancedb_index
        from tesla_finrag.ingestion.state import FilingStateEntry, IngestionState
        from tesla_finrag.ingestion.writers import write_filing_bundle

        filing = FilingDocument(
            filing_type=FilingType.QUARTERLY,
            period_end=date(2024, 6, 30),
            fiscal_year=2024,
            fiscal_quarter=2,
            accession_number="0000950170-2024-06",
            filed_at=date(2024, 7, 15),
            source_path="data/raw/Tesla_2024_Q2_10-Q.pdf",
        )
        bad_chunk = SectionChunk(
            doc_id=filing.doc_id,
            section_title="MD&A",
            text=("UNINDEXABLE " * 900).strip(),
            token_count=900,
        )
        output_dir = tmp_path / "processed"
        write_filing_bundle(filing, [bad_chunk], [], output_dir)

        class AlwaysFailProvider:
            embedding_model = "nomic-embed-text"
            base_url = "http://localhost:11434/v1"
            info = SimpleNamespace(
                provider_name="shared-indexing-backend",
                embedding_model="nomic-embed-text",
                base_url="http://localhost:11434/v1",
            )

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("400 input length exceeds the context length")

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline.IndexingEmbeddingProvider.from_settings",
            lambda: AlwaysFailProvider(),
        )

        state = IngestionState(
            filings={
                str(filing.doc_id): FilingStateEntry(
                    doc_id=filing.doc_id,
                    source_path=filing.source_path,
                    source_fingerprint="fingerprint",
                    parser_fingerprint="parser",
                    section_chunk_count=1,
                    table_chunk_count=0,
                )
            }
        )

        with pytest.raises(RuntimeError, match="Failed to index chunk after segmentation"):
            _build_lancedb_index(
                output_dir,
                state,
                refreshed_doc_ids={filing.doc_id},
                removed_doc_ids=set(),
            )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Numeric validation regression tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNumericValidation:
    """Regression tests for malformed, edge-case, and normal numeric cells."""

    def test_normalize_plain_integer(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, detail = normalize_numeric_cell("1234")
        assert value == 1234.0
        assert detail == "ok"

    def test_normalize_comma_separated(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, _ = normalize_numeric_cell("1,234,567")
        assert value == 1_234_567.0

    def test_normalize_parenthesized_negative(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, _ = normalize_numeric_cell("(1,234)")
        assert value == -1234.0

    def test_normalize_currency_prefix(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, _ = normalize_numeric_cell("$96,773")
        assert value == 96_773.0

    def test_normalize_percentage(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, detail = normalize_numeric_cell("12.5%")
        assert value == pytest.approx(0.125)
        assert detail == "percent"

    def test_normalize_scale_suffix_millions(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, detail = normalize_numeric_cell("96.8M")
        assert value == pytest.approx(96_800_000.0)
        assert "scaled" in detail

    def test_normalize_scale_suffix_billions(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, _ = normalize_numeric_cell("1.5B")
        assert value == pytest.approx(1_500_000_000.0)

    def test_normalize_dash_as_zero(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        for dash in ("—", "–", "-", "−"):
            value, detail = normalize_numeric_cell(dash)
            assert value == 0.0, f"Expected 0.0 for dash {dash!r}"
            assert detail == "dash_zero"

    def test_normalize_empty_string(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, detail = normalize_numeric_cell("")
        assert value is None
        assert detail == "empty"

    def test_normalize_non_numeric_text(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, detail = normalize_numeric_cell("Total Revenue")
        assert value is None
        assert detail == "non_numeric"

    def test_normalize_en_dash_negative(self) -> None:
        from tesla_finrag.ingestion.validation import normalize_numeric_cell

        value, _ = normalize_numeric_cell("–42")
        assert value == -42.0

    def test_is_numeric_candidate_positive(self) -> None:
        from tesla_finrag.ingestion.validation import is_numeric_candidate

        assert is_numeric_candidate("$1,234") is True
        assert is_numeric_candidate("(500)") is True
        assert is_numeric_candidate("12.5%") is True
        assert is_numeric_candidate("—") is True

    def test_is_numeric_candidate_negative(self) -> None:
        from tesla_finrag.ingestion.validation import is_numeric_candidate

        assert is_numeric_candidate("Revenue") is False
        assert is_numeric_candidate("") is False
        assert is_numeric_candidate("Item 1. Financial Statements") is False


class TestSuspiciousCellDetection:
    """Regression tests for OCR corruption detection."""

    def test_detect_ocr_I_digit_mix(self) -> None:
        from tesla_finrag.ingestion.validation import detect_suspicious_cell

        result = detect_suspicious_cell("I23456")
        assert result is not None
        assert "OCR" in result

    def test_detect_ocr_O_digit_mix(self) -> None:
        from tesla_finrag.ingestion.validation import detect_suspicious_cell

        result = detect_suspicious_cell("1O234")
        assert result is not None

    def test_clean_numeric_not_suspicious(self) -> None:
        from tesla_finrag.ingestion.validation import detect_suspicious_cell

        assert detect_suspicious_cell("1,234,567") is None
        assert detect_suspicious_cell("$96,773") is None
        assert detect_suspicious_cell("(500)") is None


class TestTableCellValidation:
    """Tests for validate_table_cells on TableChunk objects."""

    def test_validate_all_valid_cells(self) -> None:
        from tesla_finrag.ingestion.validation import (
            overall_validation_status,
            validate_table_cells,
        )
        from tesla_finrag.models import ValidationStatus

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Value"],
            rows=[["Revenue", "$96,773"], ["Net Income", "$7,928"]],
            raw_text="Metric | Value\nRevenue | $96,773\nNet Income | $7,928",
        )
        results = validate_table_cells(chunk)
        assert len(results) == 2
        assert all(r.status == ValidationStatus.VALID for r in results)
        assert overall_validation_status(results) == ValidationStatus.VALID

    def test_validate_with_failed_cell(self) -> None:
        """A cell that looks numeric but can't be parsed results in FAILED status."""
        from tesla_finrag.ingestion.validation import (
            overall_validation_status,
            validate_table_cells,
        )
        from tesla_finrag.models import ValidationStatus

        # A cell with only a dollar sign and comma — passes the numeric candidate
        # check but normalization will fail after stripping to empty.
        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Value"],
            rows=[["Revenue", "$,,,"]],
            raw_text="Metric | Value\nRevenue | $,,,",
        )
        results = validate_table_cells(chunk)
        assert len(results) >= 1
        failed = [r for r in results if r.status == ValidationStatus.FAILED]
        assert len(failed) >= 1
        assert overall_validation_status(results) == ValidationStatus.FAILED

    def test_validate_non_numeric_cells_skipped(self) -> None:
        from tesla_finrag.ingestion.validation import validate_table_cells

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Section", "Description"],
            rows=[["MD&A", "Discussion of results"]],
            raw_text="Section | Description\nMD&A | Discussion of results",
        )
        results = validate_table_cells(chunk)
        assert len(results) == 0

    def test_validate_empty_table(self) -> None:
        from tesla_finrag.ingestion.validation import (
            overall_validation_status,
            validate_table_cells,
        )
        from tesla_finrag.models import ValidationStatus

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric"],
            rows=[],
            raw_text="Metric",
        )
        results = validate_table_cells(chunk)
        assert len(results) == 0
        assert overall_validation_status(results) == ValidationStatus.NOT_CHECKED


# ═══════════════════════════════════════════════════════════════════════════
# 7. Fact reconciliation regression tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFactReconciliation:
    """Tests for reconciling table values against XBRL facts."""

    def test_reconcile_matching_value(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        doc_id = uuid4()
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "96,773"]],
            raw_text="Metric | Revenues\nFY2023 | 96,773",
        )
        results = reconcile_table_with_facts(chunk, [fact])
        assert len(results) >= 1
        assert any(r.matched for r in results)

    def test_reconcile_mismatching_value(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        doc_id = uuid4()
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "50,000"]],  # Wrong value
            raw_text="Metric | Revenues\nFY2023 | 50,000",
        )
        results = reconcile_table_with_facts(chunk, [fact])
        assert len(results) >= 1
        assert any(not r.matched for r in results)
        mismatch = next(r for r in results if not r.matched)
        assert "mismatch" in mismatch.detail

    def test_reconcile_with_period_filter(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        doc_id = uuid4()
        fact_q1 = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=23329.0,
            unit="USD",
            period_end=date(2023, 3, 31),
        )
        fact_fy = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "96,773"]],
            raw_text="Metric | Revenues\nFY2023 | 96,773",
        )
        # When filtering to Q1, the FY value should not match.
        results = reconcile_table_with_facts(
            chunk, [fact_q1, fact_fy], period_end=date(2023, 3, 31)
        )
        # Only Q1 fact considered, so all comparisons will be against 23329.
        assert all(r.period_end == date(2023, 3, 31) for r in results)

    def test_reconcile_no_matching_headers(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        doc_id = uuid4()
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Amount"],  # No "Revenues" header
            rows=[["FY2023", "96,773"]],
            raw_text="Metric | Amount\nFY2023 | 96,773",
        )
        results = reconcile_table_with_facts(chunk, [fact])
        assert len(results) == 0

    def test_reconcile_empty_facts(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "96,773"]],
            raw_text="Metric | Revenues\nFY2023 | 96,773",
        )
        results = reconcile_table_with_facts(chunk, [])
        assert len(results) == 0

    def test_reconcile_ignores_facts_from_other_filings(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        chunk_doc_id = uuid4()
        fact = FactRecord(
            doc_id=uuid4(),
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            period_end=date(2023, 12, 31),
        )
        chunk = TableChunk(
            doc_id=chunk_doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "96,773"]],
            raw_text="Metric | Revenues\nFY2023 | 96,773",
        )
        results = reconcile_table_with_facts(chunk, [fact], period_end=date(2023, 12, 31))
        assert results == []

    def test_reconcile_skips_empty_headers(self) -> None:
        from tesla_finrag.ingestion.validation import reconcile_table_with_facts

        doc_id = uuid4()
        facts = [
            FactRecord(
                doc_id=doc_id,
                concept="us-gaap:Revenues",
                label="Revenues",
                value=100.0,
                unit="USD",
                period_end=date(2023, 12, 31),
            ),
            FactRecord(
                doc_id=doc_id,
                concept="us-gaap:GrossProfit",
                label="Gross Profit",
                value=100.0,
                unit="USD",
                period_end=date(2023, 12, 31),
            ),
        ]
        chunk = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", ""],
            rows=[["FY2023", "100"]],
            raw_text="Metric |\nFY2023 | 100",
        )
        results = reconcile_table_with_facts(chunk, facts, period_end=date(2023, 12, 31))
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 8. Parser provenance and fallback tests
# ═══════════════════════════════════════════════════════════════════════════


class TestParserProvenance:
    """Tests for parser provenance tracking in chunks."""

    def test_section_chunk_default_provenance_is_none(self) -> None:
        chunk = SectionChunk(
            doc_id=uuid4(),
            section_title="Item 7. MD&A",
            text="Test content",
            token_count=2,
        )
        assert chunk.parser_provenance is None

    def test_section_chunk_with_explicit_provenance(self) -> None:
        from tesla_finrag.models import ParserProvenance

        prov = ParserProvenance(
            parser_name="pdfplumber",
            used_fallback=False,
        )
        chunk = SectionChunk(
            doc_id=uuid4(),
            section_title="Item 7. MD&A",
            text="Test content",
            token_count=2,
            parser_provenance=prov,
        )
        assert chunk.parser_provenance is not None
        assert chunk.parser_provenance.parser_name == "pdfplumber"
        assert chunk.parser_provenance.used_fallback is False

    def test_table_chunk_default_validation_status(self) -> None:
        from tesla_finrag.models import ValidationStatus

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["A"],
            rows=[["1"]],
            raw_text="A\n1",
        )
        assert chunk.validation_status == ValidationStatus.NOT_CHECKED
        assert chunk.cell_validations == []
        assert chunk.fact_reconciliations == []
        assert chunk.parser_provenance is None

    def test_table_chunk_with_fallback_provenance(self) -> None:
        from tesla_finrag.models import ParserProvenance

        prov = ParserProvenance(
            parser_name="pymupdf",
            used_fallback=True,
            fallback_reason="empty_text",
        )
        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["A"],
            rows=[["1"]],
            raw_text="A\n1",
            parser_provenance=prov,
        )
        assert chunk.parser_provenance is not None
        assert chunk.parser_provenance.used_fallback is True
        assert chunk.parser_provenance.fallback_reason == "empty_text"


class TestParserFallbackAnalysis:
    """Tests for the fallback logic in analysis.py."""

    def test_page_needs_fallback_empty_text(self) -> None:
        from tesla_finrag.ingestion.analysis import _page_needs_fallback

        assert _page_needs_fallback("", []) == "empty_text"

    def test_page_needs_fallback_insufficient_text(self) -> None:
        from tesla_finrag.ingestion.analysis import _page_needs_fallback

        assert _page_needs_fallback("Short", []) == "insufficient_text"

    def test_page_needs_fallback_normal_text(self) -> None:
        from tesla_finrag.ingestion.analysis import _page_needs_fallback

        normal_text = "This is a full paragraph of text from a financial filing."
        assert _page_needs_fallback(normal_text, []) is None

    def test_page_needs_fallback_short_text_with_tables(self) -> None:
        from tesla_finrag.ingestion.analysis import _page_needs_fallback

        # Short text but tables present → not a fallback candidate.
        assert _page_needs_fallback("Short", [[["a", "b"]]]) is None

    def test_filing_pdf_analysis_diagnostics_properties(self) -> None:
        from tesla_finrag.ingestion.analysis import (
            FilingPdfAnalysis,
            PageParserDiagnostic,
        )

        diag_ok = PageParserDiagnostic(
            page_number=1,
            parser_used="pdfplumber",
            used_fallback=False,
            text_chars=500,
        )
        diag_fallback = PageParserDiagnostic(
            page_number=2,
            parser_used="pymupdf",
            used_fallback=True,
            fallback_reason="empty_text",
            text_chars=200,
        )
        diag_error = PageParserDiagnostic(
            page_number=3,
            parser_used="pdfplumber",
            used_fallback=False,
            error="no_fallback_available: empty_text",
            text_chars=0,
        )

        analysis = FilingPdfAnalysis(
            pdf_path=Path("/tmp/test.pdf"),
            pages=(),
            diagnostics=(diag_ok, diag_fallback, diag_error),
        )
        assert analysis.fallback_count == 1
        assert len(analysis.failed_pages) == 1
        assert analysis.failed_pages[0].page_number == 3


# ═══════════════════════════════════════════════════════════════════════════
# 9. Pipeline diagnostics summary tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineDiagnostics:
    """Tests for ingestion_diagnostics in pipeline summary output."""

    def test_pipeline_summary_includes_ingestion_diagnostics(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            return (
                {},
                {},
                [],
                {
                    "fallback_pages": 2,
                    "failed_pages": 1,
                    "validation_failed_tables": 3,
                    "validation_suspect_tables": 4,
                },
            )

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        summary = run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed")

        assert "ingestion_diagnostics" in summary
        diag = summary["ingestion_diagnostics"]
        assert diag["fallback_pages"] == 2
        assert diag["failed_pages"] == 1
        assert diag["validation_failed_tables"] == 3
        assert diag["validation_suspect_tables"] == 4

    def test_pipeline_summary_empty_diagnostics_when_no_issues(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            return (
                {},
                {},
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        summary = run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed")

        diag = summary["ingestion_diagnostics"]
        assert all(v == 0 for v in diag.values())

    def test_pipeline_summary_includes_parser_diagnostic_details(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, object]]:
            return (
                {},
                {},
                [],
                {
                    "fallback_pages": 1,
                    "failed_pages": 1,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                    "parser_diagnostic_details": [
                        {
                            "period_key": "FY2023",
                            "source_path": "data/raw/Tesla_2023_全年_10-K.pdf",
                            "artifact_type": "page",
                            "page_number": 7,
                            "parser_used": "pdfplumber",
                            "parser_attempts": ["pdfplumber", "pymupdf(unavailable)"],
                            "used_fallback": False,
                            "fallback_reason": "empty_text",
                            "error": "no_fallback_available: empty_text",
                            "remediation": (
                                "Install the optional fallback parser "
                                "(`uv sync --extra fallback`) or review the source PDF manually."
                            ),
                        }
                    ],
                },
            )

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        summary = run_pipeline(raw_dir=raw_dir, output_dir=raw_dir.parent / "processed")

        assert "parser_diagnostic_details" in summary
        details = summary["parser_diagnostic_details"]
        assert len(details) == 1
        assert details[0]["page_number"] == 7
        assert "remediation" in details[0]


# ═══════════════════════════════════════════════════════════════════════════
# 10. Citation-ready table metadata tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCitationReadyTableMetadata:
    """Verify table chunks carry all metadata needed for citation review."""

    def test_table_chunk_serializes_provenance_and_validation(self) -> None:
        from tesla_finrag.models import (
            CellValidationResult,
            FactReconciliationResult,
            ParserProvenance,
            ValidationStatus,
        )

        prov = ParserProvenance(parser_name="pdfplumber", used_fallback=False)
        cell_val = CellValidationResult(
            row_index=0,
            col_index=1,
            raw_value="$96,773",
            normalized_value=96773.0,
            status=ValidationStatus.VALID,
            detail="ok",
        )
        fact_rec = FactReconciliationResult(
            concept="us-gaap:Revenues",
            period_end=date(2023, 12, 31),
            table_value=96773.0,
            fact_value=96773.0,
            matched=True,
            detail="match",
        )
        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Revenue"],
            rows=[["FY2023", "$96,773"]],
            raw_text="Metric | Revenue\nFY2023 | $96,773",
            parser_provenance=prov,
            validation_status=ValidationStatus.VALID,
            cell_validations=[cell_val],
            fact_reconciliations=[fact_rec],
        )

        data = chunk.model_dump(mode="json")

        # Provenance serialized.
        assert data["parser_provenance"]["parser_name"] == "pdfplumber"
        assert data["parser_provenance"]["used_fallback"] is False

        # Validation status serialized.
        assert data["validation_status"] == "valid"

        # Cell validations serialized.
        assert len(data["cell_validations"]) == 1
        assert data["cell_validations"][0]["normalized_value"] == 96773.0

        # Fact reconciliations serialized.
        assert len(data["fact_reconciliations"]) == 1
        assert data["fact_reconciliations"][0]["matched"] is True

    def test_table_chunk_roundtrips_through_json(self) -> None:
        from tesla_finrag.models import (
            CellValidationResult,
            ParserProvenance,
            ValidationStatus,
        )

        prov = ParserProvenance(
            parser_name="pymupdf",
            used_fallback=True,
            fallback_reason="empty_text",
        )
        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["A", "B"],
            rows=[["x", "100"]],
            raw_text="A | B\nx | 100",
            parser_provenance=prov,
            validation_status=ValidationStatus.SUSPECT,
            cell_validations=[
                CellValidationResult(
                    row_index=0,
                    col_index=1,
                    raw_value="100",
                    normalized_value=100.0,
                    status=ValidationStatus.VALID,
                    detail="ok",
                ),
            ],
        )

        data = chunk.model_dump(mode="json")
        restored = TableChunk.model_validate(data)

        assert restored.parser_provenance is not None
        assert restored.parser_provenance.used_fallback is True
        assert restored.parser_provenance.fallback_reason == "empty_text"
        assert restored.validation_status == ValidationStatus.SUSPECT
        assert len(restored.cell_validations) == 1
        assert restored.cell_validations[0].normalized_value == 100.0


# ═══════════════════════════════════════════════════════════════════════════
# 11. OCR-corrupted non-numeric-candidate cell detection (Fix 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestOCRCorruptedCellDetection:
    """Validate that OCR-corrupted cells that fail is_numeric_candidate() are
    still flagged as SUSPECT when they contain significant digit content."""

    def test_ocr_corrupted_cell_with_letter_I_flagged_as_suspect(self) -> None:
        from tesla_finrag.ingestion.validation import validate_table_cells
        from tesla_finrag.models import ValidationStatus

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Value"],
            rows=[["Revenue", "51,6I8"]],
            raw_text="Metric | Value\nRevenue | 51,6I8",
        )
        results = validate_table_cells(chunk)

        suspect_results = [r for r in results if r.status == ValidationStatus.SUSPECT]
        assert len(suspect_results) == 1
        assert suspect_results[0].raw_value == "51,6I8"
        assert suspect_results[0].normalized_value is None
        assert "OCR suspect" in suspect_results[0].detail

    def test_ocr_corrupted_cell_with_letter_O_flagged_as_suspect(self) -> None:
        from tesla_finrag.ingestion.validation import validate_table_cells
        from tesla_finrag.models import ValidationStatus

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Value"],
            rows=[["Cost", "1O3,456"]],
            raw_text="Metric | Value\nCost | 1O3,456",
        )
        results = validate_table_cells(chunk)

        suspect_results = [r for r in results if r.status == ValidationStatus.SUSPECT]
        assert len(suspect_results) == 1
        assert suspect_results[0].raw_value == "1O3,456"

    def test_text_label_with_single_digit_not_flagged(self) -> None:
        """Labels like 'Item 1' have only 1 digit — not enough to be suspicious."""
        from tesla_finrag.ingestion.validation import validate_table_cells

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Metric", "Value"],
            rows=[["Item 1", "100"]],
            raw_text="Metric | Value\nItem 1 | 100",
        )
        results = validate_table_cells(chunk)

        # Only "100" should produce a result (VALID numeric), not "Item 1"
        assert all(r.raw_value != "Item 1" for r in results)

    def test_has_significant_digits_helper(self) -> None:
        from tesla_finrag.ingestion.validation import _has_significant_digits

        assert _has_significant_digits("51,6I8") is True
        assert _has_significant_digits("1O3") is True
        assert _has_significant_digits("Item 1") is False
        assert _has_significant_digits("abc") is False
        assert _has_significant_digits("12") is True
        assert _has_significant_digits("5") is False

    def test_non_ocr_text_with_digits_not_flagged(self) -> None:
        """Text like 'FY2023' has digits but no OCR pattern — should not be flagged."""
        from tesla_finrag.ingestion.validation import validate_table_cells

        chunk = TableChunk(
            doc_id=uuid4(),
            section_title="Item 1",
            headers=["Period", "Value"],
            rows=[["FY2023", "100"]],
            raw_text="Period | Value\nFY2023 | 100",
        )
        results = validate_table_cells(chunk)

        # FY2023 has 4 digits and passes _has_significant_digits, but
        # detect_suspicious_cell should NOT flag it (no OCR substitution patterns).
        assert all(r.raw_value != "FY2023" for r in results)


# ═══════════════════════════════════════════════════════════════════════════
# 12. Pipeline fact reconciliation wiring (Fix 1)
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineFactReconciliation:
    """Verify that reconcile_table_with_facts is wired into the pipeline."""

    def test_reconcile_filing_tables_attaches_reconciliations(self) -> None:
        from tesla_finrag.ingestion.pipeline import _reconcile_filing_tables

        doc_id = uuid4()
        filing = FilingDocument(
            doc_id=doc_id,
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            fiscal_year=2023,
            accession_number="0001-23-000001",
            filed_at=date(2024, 2, 1),
            source_path="data/raw/test.pdf",
        )
        table = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "$96,773"]],
            raw_text="Metric | Revenues\nFY2023 | $96,773",
        )
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            scale=1,
            period_end=date(2023, 12, 31),
        )

        all_table_chunks = {doc_id: [table]}
        updated, mismatches = _reconcile_filing_tables(all_table_chunks, [filing], [fact])

        assert mismatches == 0
        assert len(updated[doc_id][0].fact_reconciliations) == 1
        assert updated[doc_id][0].fact_reconciliations[0].matched is True

    def test_reconcile_filing_tables_counts_mismatches(self) -> None:
        from tesla_finrag.ingestion.pipeline import _reconcile_filing_tables

        doc_id = uuid4()
        filing = FilingDocument(
            doc_id=doc_id,
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            fiscal_year=2023,
            accession_number="0001-23-000001",
            filed_at=date(2024, 2, 1),
            source_path="data/raw/test.pdf",
        )
        table = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "$90,000"]],
            raw_text="Metric | Revenues\nFY2023 | $90,000",
        )
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            scale=1,
            period_end=date(2023, 12, 31),
        )

        all_table_chunks = {doc_id: [table]}
        updated, mismatches = _reconcile_filing_tables(all_table_chunks, [filing], [fact])

        assert mismatches == 1
        assert updated[doc_id][0].fact_reconciliations[0].matched is False

    def test_reconcile_filing_tables_marks_validation_failed_on_mismatch(self) -> None:
        from tesla_finrag.ingestion.pipeline import _reconcile_filing_tables
        from tesla_finrag.models import ValidationStatus

        doc_id = uuid4()
        filing = FilingDocument(
            doc_id=doc_id,
            filing_type=FilingType.ANNUAL,
            period_end=date(2023, 12, 31),
            fiscal_year=2023,
            accession_number="0001-23-000001",
            filed_at=date(2024, 2, 1),
            source_path="data/raw/test.pdf",
        )
        table = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["Metric", "Revenues"],
            rows=[["FY2023", "$90,000"]],
            raw_text="Metric | Revenues\nFY2023 | $90,000",
        )
        fact = FactRecord(
            doc_id=doc_id,
            concept="us-gaap:Revenues",
            label="Revenues",
            value=96773.0,
            unit="USD",
            scale=1,
            period_end=date(2023, 12, 31),
        )

        updated, mismatches = _reconcile_filing_tables({doc_id: [table]}, [filing], [fact])

        assert mismatches == 1
        assert updated[doc_id][0].validation_status == ValidationStatus.FAILED

    def test_reconcile_filing_tables_no_facts_returns_zero_mismatches(self) -> None:
        from tesla_finrag.ingestion.pipeline import _reconcile_filing_tables

        doc_id = uuid4()
        table = TableChunk(
            doc_id=doc_id,
            section_title="Item 1",
            headers=["A", "B"],
            rows=[["x", "100"]],
            raw_text="A | B\nx | 100",
        )

        updated, mismatches = _reconcile_filing_tables({doc_id: [table]}, [], [])

        assert mismatches == 0
        assert updated[doc_id][0].fact_reconciliations == []

    def test_load_facts_from_disk_roundtrips(self, tmp_path: Path) -> None:
        from tesla_finrag.ingestion.pipeline import _load_facts_from_disk
        from tesla_finrag.ingestion.writers import write_facts

        facts = [
            FactRecord(
                doc_id=uuid4(),
                concept="us-gaap:Revenues",
                label="Revenues",
                value=96773.0,
                unit="USD",
                scale=1,
                period_end=date(2023, 12, 31),
            ),
            FactRecord(
                doc_id=uuid4(),
                concept="us-gaap:NetIncome",
                label="Net Income",
                value=15000.0,
                unit="USD",
                scale=1,
                period_end=date(2023, 12, 31),
            ),
        ]
        write_facts(facts, tmp_path)
        loaded = _load_facts_from_disk(tmp_path)

        assert len(loaded) == 2
        assert loaded[0].concept == "us-gaap:Revenues"
        assert loaded[1].concept == "us-gaap:NetIncome"

    def test_load_facts_from_disk_returns_empty_when_missing(self, tmp_path: Path) -> None:
        from tesla_finrag.ingestion.pipeline import _load_facts_from_disk

        loaded = _load_facts_from_disk(tmp_path)

        assert loaded == []

    def test_pipeline_reconciliation_diagnostics_in_summary(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify reconciliation mismatches appear in the pipeline summary."""
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                section_map[filing.doc_id] = [
                    SectionChunk(
                        doc_id=filing.doc_id,
                        section_title="Test",
                        text="test",
                        token_count=1,
                    )
                ]
                table_map[filing.doc_id] = [
                    TableChunk(
                        doc_id=filing.doc_id,
                        section_title="Item 1",
                        headers=["Metric", "Revenues"],
                        rows=[["FY2023", "$90,000"]],
                        raw_text="Metric | Revenues\nFY2023 | $90,000",
                    )
                ]
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        # Create a companyfacts.json with a mismatched revenue value.
        companyfacts = {
            "cik": 1318605,
            "entityName": "Tesla, Inc.",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": 96773,
                                    "accn": "0001-23-000001",
                                    "form": "10-K",
                                    "fy": 2023,
                                    "fp": "FY",
                                }
                            ]
                        },
                    }
                }
            },
        }
        (raw_dir / "companyfacts.json").write_text(json.dumps(companyfacts))

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        summary = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)

        diag = summary["ingestion_diagnostics"]
        assert "fact_reconciliation_mismatches" in diag
        assert diag["fact_reconciliation_mismatches"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# 13. Facts loading during reuse (Fix 1 - reuse path)
# ═══════════════════════════════════════════════════════════════════════════


class TestFactsLoadDuringReuse:
    """Verify that facts are loaded from disk when reusing companyfacts output."""

    def test_pipeline_loads_facts_from_disk_on_reuse(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                section_map[filing.doc_id] = [
                    SectionChunk(
                        doc_id=filing.doc_id,
                        section_title="Test",
                        text="test",
                        token_count=1,
                    )
                ]
                table_map[filing.doc_id] = [
                    TableChunk(
                        doc_id=filing.doc_id,
                        section_title="Item 1",
                        headers=["A", "B"],
                        rows=[["x", "100"]],
                        raw_text="A | B\nx | 100",
                    )
                ]
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        # Create a minimal companyfacts.json.
        companyfacts = {
            "cik": 1318605,
            "entityName": "Tesla, Inc.",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": 96773,
                                    "accn": "0001-23-000001",
                                    "form": "10-K",
                                    "fy": 2023,
                                    "fp": "FY",
                                }
                            ]
                        },
                    }
                }
            },
        }
        (raw_dir / "companyfacts.json").write_text(json.dumps(companyfacts))

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"

        # First run: normalizes and writes facts.
        first = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        assert first["facts_reused"] is False
        assert first["fact_records"] > 0

        # Second run: should reuse facts from disk.
        second = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        assert second["facts_reused"] is True
        assert second["fact_records"] > 0

    def test_pipeline_reconciles_reused_tables_when_facts_change(
        self, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tesla_finrag.ingestion.pipeline import run_pipeline

        def fake_run_jobs(
            filings: list[FilingDocument],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict], dict[str, int]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                section_map[filing.doc_id] = [
                    SectionChunk(
                        doc_id=filing.doc_id,
                        section_title="Test",
                        text="test",
                        token_count=1,
                    )
                ]
                table_map[filing.doc_id] = [
                    TableChunk(
                        doc_id=filing.doc_id,
                        section_title="Item 1",
                        headers=["Metric", "Revenues"],
                        rows=[["FY2023", "100"]],
                        raw_text="Metric | Revenues\nFY2023 | 100",
                    )
                ]
            return (
                section_map,
                table_map,
                [],
                {
                    "fallback_pages": 0,
                    "failed_pages": 0,
                    "validation_failed_tables": 0,
                    "validation_suspect_tables": 0,
                },
            )

        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._run_filing_ingestion_jobs",
            fake_run_jobs,
        )
        monkeypatch.setattr(
            "tesla_finrag.ingestion.pipeline._build_lancedb_index",
            lambda *args, **kwargs: 0,
        )

        output_dir = raw_dir.parent / "processed"
        companyfacts = {
            "cik": 1318605,
            "entityName": "Tesla, Inc.",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": 100,
                                    "accn": "0001-23-000001",
                                    "form": "10-K",
                                    "fy": 2023,
                                    "fp": "FY",
                                }
                            ]
                        },
                    }
                }
            },
        }
        companyfacts_path = raw_dir / "companyfacts.json"
        companyfacts_path.write_text(json.dumps(companyfacts))

        first = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        assert first["facts_reused"] is False

        companyfacts["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["val"] = 250
        companyfacts_path.write_text(json.dumps(companyfacts))

        second = run_pipeline(raw_dir=raw_dir, output_dir=output_dir)
        assert second["facts_reused"] is False
        assert second["ingestion_diagnostics"]["fact_reconciliation_mismatches"] > 0

        tables_dir = output_dir / "tables"
        table_files = sorted(tables_dir.rglob("*.json"))
        assert table_files
        persisted_chunks = [
            TableChunk.model_validate_json(path.read_text(encoding="utf-8")) for path in table_files
        ]
        assert any(chunk.validation_status == "failed" for chunk in persisted_chunks)
        mismatched = next(chunk for chunk in persisted_chunks if chunk.fact_reconciliations)
        assert mismatched.fact_reconciliations[0].matched is False
