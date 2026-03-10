## ADDED Requirements

### Requirement: Persistent LanceDB retrieval index generation
The ingestion system SHALL build and refresh a LanceDB retrieval index under the processed-corpus output root using the normalized section and table chunks generated for the filing corpus.

#### Scenario: Build the retrieval index during ingestion
- **WHEN** an operator runs the supported ingestion CLI against a valid raw corpus
- **THEN** the system writes or updates a LanceDB artifact under the processed output location that contains the chunk rows needed for runtime vector retrieval

#### Scenario: Refresh stale retrieval rows on re-ingest
- **WHEN** ingestion reprocesses filings whose normalized chunks changed since the previous run
- **THEN** the system updates the corresponding LanceDB rows so runtime retrieval uses the latest processed chunk content

## MODIFIED Requirements

### Requirement: Ingestion summary reporting
The ingestion CLI SHALL report a concise completion summary that includes the processed output location, LanceDB index status or location, normalized record counts, and any manifest gaps detected during the run.

#### Scenario: Review ingestion results
- **WHEN** the ingestion CLI completes successfully
- **THEN** the operator can see what processed artifacts were generated, whether the LanceDB index was built or refreshed, and whether any expected filings were missing
