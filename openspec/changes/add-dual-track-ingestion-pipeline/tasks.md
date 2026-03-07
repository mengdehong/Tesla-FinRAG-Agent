## 1. Source inventory and manifest

- [ ] 1.1 Implement the Tesla filing manifest builder that enumerates the target filing set and records available, downloadable, and missing sources.
- [ ] 1.2 Add source adapters or download utilities that reconcile existing local raw files with SEC-based source discovery.

## 2. Narrative and table normalization

- [ ] 2.1 Implement section-aware narrative parsing that emits normalized narrative chunks with provenance metadata.
- [ ] 2.2 Implement table extraction and normalization so each table is stored as an independent chunk with structured metadata.

## 3. XBRL fact normalization

- [ ] 3.1 Implement companyfacts/XBRL normalization into typed fact records aligned by metric, unit, source form, and `period_key`.
- [ ] 3.2 Implement processed data writers for normalized filings, chunks, tables, and facts outside `data/raw/`.

## 4. Regression validation

- [ ] 4.1 Add ingestion tests covering manifest coverage, missing filing detection, section/table metadata, and `period_key` handling.
- [ ] 4.2 Verify the normalized corpus can be produced from the current repository data and that known coverage gaps are reported explicitly.
