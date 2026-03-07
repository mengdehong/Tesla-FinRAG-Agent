## Context

The project needs a corpus that supports two different evidence modes. Narrative text and tables from Tesla 10-K and 10-Q filings are required for grounded citations and management commentary, while authoritative financial values must come from structured XBRL/companyfacts data to avoid brittle table parsing for numeric calculations. The repository already contains many raw PDFs and one downloaded `companyfacts.json`, but it lacks a manifest, normalized storage layout, consistent period keys, and regression checks.

## Goals / Non-Goals

**Goals:**

- Enumerate the target filing set and record what is present versus missing.
- Normalize narrative sections and tables from filing sources into retrieval-ready chunks.
- Normalize structured XBRL/companyfacts facts into period-aligned records suitable for later calculations.
- Store normalized outputs in a consistent local layout that downstream retrieval and evaluation changes can reuse.
- Add ingestion regression tests around parsing, coverage, metadata, and period handling.

**Non-Goals:**

- Build the hybrid retrieval index itself.
- Generate final answers or UI displays.
- Solve every possible SEC parsing edge case up front; the focus is a strong, traceable first-pass pipeline.

## Decisions

### Decision: use a dual-track corpus instead of a PDF-only corpus

Narrative evidence will come from filing HTML/PDF parsing, while numerical facts will come from XBRL/companyfacts normalization. This matches the project decision record and reduces calculation risk caused by fragile PDF table extraction.

Alternative considered: rely only on PDF parsing for both text and numbers. Rejected because it would make metric extraction error-prone and obscure the line between narrative evidence and numeric authority.

### Decision: maintain an explicit manifest with gap reporting

The ingestion pipeline will enumerate the expected Tesla filings and record which documents are available locally, downloadable, or missing. Missing data, such as the currently absent 2025 FY 10-K, must be surfaced as a manifest gap rather than hidden by partial ingestion.

Alternative considered: ingest whatever files happen to exist under `data/raw/`. Rejected because later evaluation would not know whether missing evidence reflects a retrieval failure or incomplete source coverage.

### Decision: normalize all outputs around `period_key` and source metadata

Every narrative chunk, table chunk, and fact record will carry consistent time and source metadata such as form type, fiscal year, fiscal quarter when applicable, filing date, accession/source identifier, and page or section provenance. This is necessary for later metadata filtering, citations, and text-to-fact linking.

Alternative considered: let each parser emit its own metadata shape and reconcile later. Rejected because retrieval and calculation logic depend on a shared period model.

### Decision: store normalized outputs in repository-owned processed locations

Derived artifacts will be written outside `data/raw/` into normalized and derived paths managed by the ingestion layer. This preserves raw inputs as immutable source data and aligns with the repository guidance.

Alternative considered: write cleaned outputs back into `data/raw/`. Rejected because it would blur source-of-truth boundaries and make regression analysis harder.

## Risks / Trade-offs

- [Risk] HTML and PDF structures may differ across filings. -> Mitigation: normalize to shared chunk models and keep parser-specific logic behind adapters.
- [Risk] Some metrics in `companyfacts` may require additional filtering or context. -> Mitigation: preserve units, form references, and period metadata for later selection logic.
- [Risk] Table extraction may still be imperfect for some filings. -> Mitigation: keep tables independent from narrative chunks and attach provenance for debugging and manual inspection.

## Migration Plan

1. Build the target filing manifest and local source inventory.
2. Implement source adapters for available filing formats and existing local data.
3. Normalize narrative sections and tables into chunk records.
4. Normalize XBRL/companyfacts into fact records with aligned period keys.
5. Persist normalized outputs under processed paths and add regression tests.
6. Hand off the normalized corpus contract to the retrieval change.

## Open Questions

- Whether the first-pass implementation should prefer SEC HTML over local PDF when both are available can be decided during implementation, but the normalized output contract must remain source-agnostic.
- If a filing lacks a reliable PDF, the pipeline should still ingest narrative evidence from HTML without blocking the entire manifest.
