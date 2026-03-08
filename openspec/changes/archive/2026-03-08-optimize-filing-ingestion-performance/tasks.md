## 1. Shared PDF Analysis Path

- [x] 1.1 Introduce a filing-level PDF analysis helper that opens each filing once, extracts page text once, and provides shared section context for both narrative and table normalization.
- [x] 1.2 Update the ingestion pipeline worker flow to consume the shared filing analysis result while preserving the existing `SectionChunk` and `TableChunk` semantics.
- [x] 1.3 Add regression tests that compare representative 10-Q and 10-K outputs before and after the refactor so section detection, chunking, and table metadata stay stable.

## 2. Incremental Reuse And Reporting

- [x] 2.1 Add ingestion state models and source fingerprint helpers for filing PDFs and `companyfacts.json`.
- [x] 2.2 Reuse unchanged filing and fact artifacts on rerun, and selectively invalidate plus rewrite only the entries whose source fingerprint or parser fingerprint changed.
- [x] 2.3 Extend `python -m tesla_finrag ingest` reporting and logs with separate counts for reprocessed, reused, and failed filings.
- [x] 2.4 Make default worker resolution depend on the number of filings that still require parsing after reuse checks while preserving explicit operator overrides.

## 3. Validation And Operator Guidance

- [x] 3.1 Add ingestion tests covering unchanged reruns, single-filing invalidation, `companyfacts` invalidation, and missing or stale state fallback behavior.
- [x] 3.2 Update repository documentation to explain incremental ingestion behavior, reuse-aware summaries, and the supported way to force a full rebuild.
- [x] 3.3 Run the relevant validation commands and confirm the optimized ingestion path still produces a runtime-compatible processed corpus.
