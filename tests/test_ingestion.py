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
        # We have 2 valid Revenue entries + 1 Assets + 1 dei entry = 4.
        assert len(records) == 4

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

        def fake_run_jobs(
            filings: list[object],
            raw_dir_value: Path,
            *,
            workers: int,
        ) -> tuple[dict, dict, list[dict]]:
            calls.append(workers)
            if workers > 1:
                raise PermissionError("sandbox blocked multiprocessing")
            return {}, {}, []

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
        ) -> tuple[dict, dict, list[dict]]:
            calls.append([filing.source_path for filing in filings])
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return section_map, table_map, []

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
        ) -> tuple[dict, dict, list[dict]]:
            calls.append([Path(filing.source_path).name for filing in filings])
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return section_map, table_map, []

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
        ) -> tuple[dict, dict, list[dict]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return section_map, table_map, []

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
        ) -> tuple[dict, dict, list[dict]]:
            calls.append(len(filings))
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return section_map, table_map, []

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
        ) -> tuple[dict, dict, list[dict]]:
            section_map: dict = {}
            table_map: dict = {}
            for filing in filings:
                sections, tables = self._fake_chunks(filing)
                section_map[filing.doc_id] = sections
                table_map[filing.doc_id] = tables
            return section_map, table_map, []

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
                "embedding_model": "nomic-embed-text",
                "embedding_base_url": "http://localhost:11434/v1",
                "embedding_dimensions": 3,
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
