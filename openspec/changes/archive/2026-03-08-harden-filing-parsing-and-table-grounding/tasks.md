## 1. Parsing provenance and validation

- [x] 1.1 Extend ingestion models and processed artifact writers to record parser provenance and validation metadata for narrative and table outputs.
- [x] 1.2 Implement numeric normalization, suspicious-cell detection, and authoritative fact reconciliation helpers for extracted financial tables.

## 2. Ingestion integration

- [x] 2.1 Add parser fallback handling and source-aware diagnostics to the filing analysis and table extraction flow.
- [x] 2.2 Surface validation failures, mismatch summaries, and fallback paths in ingestion reporting without degrading existing processed-corpus contracts.

## 3. Validation and operator guidance

- [x] 3.1 Add regression tests for malformed numeric cells, fact-versus-table mismatch handling, parser fallback provenance, and citation-ready table metadata.
- [x] 3.2 Update operator documentation and run targeted ingestion/runtime validation for the hardened parsing workflow.
