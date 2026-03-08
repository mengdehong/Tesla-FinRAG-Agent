## Why

The ingestion pipeline can already extract narrative chunks, tables, and XBRL facts, but it does not yet distinguish trustworthy table output from malformed extraction. As a result, downstream retrieval and answer generation can still consume garbled numeric cells or weak table provenance, which undermines grounded financial answers and failure analysis.

## What Changes

- Harden filing parsing so narrative and table extraction record parser provenance, fallback behavior, and source-aware diagnostics.
- Add numeric validation for extracted table cells and reconcile authoritative concepts against normalized XBRL facts when both are available.
- Preserve complete table units and improve caption, section, and page provenance so retrieval and citations can trace table evidence reliably.
- Expand ingestion reporting and regression coverage for parser failures, invalid numeric cells, and fact-versus-table mismatches.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `filing-ingestion`: table and narrative normalization requirements expand to include parser provenance, numeric validation, and authoritative fact reconciliation for extracted financial tables.

## Impact

- Affected code: ingestion analysis helpers, table normalization, narrative provenance handling, processed artifact writers, and ingestion diagnostics.
- Affected APIs: processed table chunk metadata and ingestion summary outputs may gain validation and provenance fields.
- Dependencies: may add a local parser fallback dependency such as PyMuPDF, but should remain optional unless it materially improves extraction reliability.
- Systems: `data/raw/` to `data/processed/` normalization quality, grounded citations, and downstream QA trustworthiness.
