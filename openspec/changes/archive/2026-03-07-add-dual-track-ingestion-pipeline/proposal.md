## Why

The project cannot answer cross-period Tesla financial questions unless it first builds a reliable, traceable corpus from both narrative filings and structured financial facts. The current repository has raw PDFs and a single exploratory script, but no normalized filing manifest, no section-aware chunking, and no authoritative fact pipeline for calculations.

## What Changes

- Add a filing manifest and source adapter flow that enumerates target Tesla filings and records coverage gaps explicitly.
- Add narrative extraction for filing sections and tables with source metadata suitable for citation and retrieval.
- Add XBRL/companyfacts normalization so financial metrics can be aligned by period and unit.
- Add normalized data outputs and regression checks that future retrieval and answer changes can consume directly.

## Capabilities

### New Capabilities
- `filing-ingestion`: A dual-track ingestion pipeline that produces a normalized filing corpus from narrative SEC filings and structured XBRL facts.

### Modified Capabilities
- None.

## Impact

- Affected code: future ingestion modules, normalized data writers, parsing utilities, and ingestion tests.
- Affected APIs: corpus and fact repository inputs for later retrieval and calculation stages.
- Dependencies: SEC access libraries or HTTP clients, HTML/PDF parsers, and structured data utilities.
- Systems: raw filing handling, normalized corpus generation, and data completeness reporting.
