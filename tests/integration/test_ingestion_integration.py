"""Integration test: verify the normalized corpus can be produced from repository data.

Runs the full dual-track ingestion pipeline against the actual ``data/raw/``
files and asserts that:
- All 20 target filings are detected in the manifest
- Narrative chunks are produced for every available filing
- Table chunks are extracted from financial statement pages
- XBRL facts are normalised with correct period alignment
- Coverage gaps (if any) are reported explicitly
- All outputs are written outside ``data/raw/``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Guard: skip if data/raw/ doesn't have real filings
# ═══════════════════════════════════════════════════════════════════════════

_RAW_DIR = Path("data/raw")
_HAS_DATA = _RAW_DIR.is_dir() and any(_RAW_DIR.glob("Tesla_*.pdf"))


@pytest.mark.skipif(not _HAS_DATA, reason="Requires data/raw/ with Tesla PDFs")
class TestIngestionPipelineIntegration:
    """End-to-end ingestion pipeline validation."""

    def test_manifest_covers_all_target_filings(self) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(_RAW_DIR)
        # 5 years * 4 filings each = 20.
        assert manifest.total == 20

    def test_all_local_filings_are_available(self) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest

        manifest = build_manifest(_RAW_DIR)
        # With the current data, all 20 should be present.
        assert manifest.available_count == 20
        assert manifest.gap_count == 0

    def test_filing_resolution_produces_20_documents(self) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest
        from tesla_finrag.ingestion.source_adapter import resolve_all_filings

        manifest = build_manifest(_RAW_DIR)
        filings = resolve_all_filings(manifest)
        assert len(filings) == 20

    def test_doc_ids_are_unique(self) -> None:
        from tesla_finrag.ingestion.manifest import build_manifest
        from tesla_finrag.ingestion.source_adapter import resolve_all_filings

        manifest = build_manifest(_RAW_DIR)
        filings = resolve_all_filings(manifest)
        ids = [f.doc_id for f in filings]
        assert len(set(ids)) == len(ids), "doc_ids must be unique"

    def test_narrative_chunks_produced_for_sample_filing(self) -> None:
        from uuid import uuid4

        from tesla_finrag.ingestion.narrative import parse_narrative

        pdf = _RAW_DIR / "Tesla_2023_Q1_10-Q.pdf"
        chunks = parse_narrative(pdf, uuid4())
        assert len(chunks) > 10, "Expected substantial narrative content"
        # Should contain MD&A section.
        section_titles = {c.section_title for c in chunks}
        has_mda = any("MANAGEMENT" in t.upper() or "MD&A" in t.upper() for t in section_titles)
        assert has_mda, f"Expected MD&A section; got {section_titles}"

    def test_table_chunks_produced_for_sample_filing(self) -> None:
        from uuid import uuid4

        from tesla_finrag.ingestion.tables import extract_tables

        pdf = _RAW_DIR / "Tesla_2023_Q1_10-Q.pdf"
        chunks = extract_tables(pdf, uuid4())
        assert len(chunks) > 5, "Expected multiple financial tables"
        # At least one table should have a balance sheet caption.
        captions = [c.caption for c in chunks if c.caption]
        has_balance_sheet = any("Balance Sheet" in cap for cap in captions)
        assert has_balance_sheet, f"Expected balance sheet table; captions={captions}"

    def test_xbrl_facts_normalized(self) -> None:
        companyfacts = _RAW_DIR / "companyfacts.json"
        if not companyfacts.exists():
            pytest.skip("companyfacts.json not available")

        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts)
        assert len(records) > 1000, "Expected substantial fact records"

        # Check for key financial metrics.
        concepts = {r.concept for r in records}
        assert "us-gaap:Revenues" in concepts
        assert "us-gaap:NetIncomeLoss" in concepts
        assert "us-gaap:Assets" in concepts

    def test_xbrl_period_key_alignment(self) -> None:
        companyfacts = _RAW_DIR / "companyfacts.json"
        if not companyfacts.exists():
            pytest.skip("companyfacts.json not available")

        from datetime import date

        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        records = normalize_companyfacts(companyfacts)
        # FY2023 revenue should exist.
        fy2023_rev = [
            r
            for r in records
            if r.concept == "us-gaap:Revenues"
            and r.period_end == date(2023, 12, 31)
            and r.period_start == date(2023, 1, 1)
        ]
        assert len(fy2023_rev) >= 1
        # Value should be ~96.8B.
        assert fy2023_rev[0].value == pytest.approx(96_773_000_000, rel=0.01)

    def test_xbrl_doc_ids_match_manifest(self) -> None:
        companyfacts = _RAW_DIR / "companyfacts.json"
        if not companyfacts.exists():
            pytest.skip("companyfacts.json not available")

        from tesla_finrag.ingestion.manifest import build_manifest
        from tesla_finrag.ingestion.source_adapter import resolve_all_filings
        from tesla_finrag.ingestion.xbrl import normalize_companyfacts

        manifest = build_manifest(_RAW_DIR)
        filings = resolve_all_filings(manifest)
        filing_ids = {f.doc_id for f in filings}

        records = normalize_companyfacts(companyfacts)
        fact_doc_ids = {r.doc_id for r in records}

        # At least some fact doc_ids should match filing doc_ids.
        overlap = filing_ids & fact_doc_ids
        assert len(overlap) > 0, "XBRL facts should link to known filings"

    def test_full_pipeline_writes_outputs(self) -> None:
        """Run the full pipeline and verify output files are created."""
        from tesla_finrag.ingestion.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            summary = run_pipeline(
                raw_dir=_RAW_DIR,
                output_dir=out_dir,
            )

            # Check summary counts.
            assert summary["manifest_entries"] == 20
            assert summary["filings"] == 20
            assert summary["section_chunks"] > 100
            assert summary["table_chunks"] > 50
            assert summary["fact_records"] > 1000

            # Check output files exist.
            assert (out_dir / "manifest.json").exists()
            assert len(list((out_dir / "filings").glob("*.json"))) == 20
            assert len(list((out_dir / "chunks").iterdir())) == 20  # One dir per filing.
            assert (out_dir / "facts" / "all_facts.jsonl").exists()

            # Verify manifest JSON is valid.
            manifest_data = json.loads((out_dir / "manifest.json").read_text())
            assert len(manifest_data["entries"]) == 20

    def test_gap_reporting_with_reduced_target(self) -> None:
        """Verify that gap reporting works by targeting years beyond available data."""
        from tesla_finrag.ingestion.manifest import build_manifest

        # Target 2026 which has no data.
        manifest = build_manifest(_RAW_DIR, years=range(2026, 2027))
        assert manifest.total == 4  # 1 annual + 3 quarterly
        assert manifest.gap_count == 4
        for gap in manifest.gaps:
            assert gap.status.value in ("downloadable", "missing")
            assert gap.fiscal_year == 2026
