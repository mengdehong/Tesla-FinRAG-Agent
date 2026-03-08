## MODIFIED Requirements

### Requirement: Persistent LanceDB retrieval index generation
The ingestion system SHALL build and refresh a LanceDB retrieval index under the
processed-corpus output root using vector rows derived from the normalized
section and table chunks generated for the filing corpus. When a normalized
chunk exceeds the safe embedding input budget of the configured indexing
backend, the system SHALL segment that chunk into multiple embedding-safe vector
rows while preserving lineage back to the original processed chunk record.

#### Scenario: Build the retrieval index during ingestion
- **WHEN** an operator runs the supported ingestion CLI against a valid raw
  corpus
- **THEN** the system writes or updates a LanceDB artifact under the processed
  output location that contains the vector rows needed for runtime semantic
  retrieval

#### Scenario: Segment an oversized chunk for indexing
- **WHEN** a normalized section or table chunk is too large for the configured
  embedding backend to accept as a single input
- **THEN** the system writes multiple LanceDB rows for that chunk and records
  source-chunk lineage metadata for each segment instead of failing immediately

#### Scenario: Refresh stale retrieval rows on re-ingest
- **WHEN** ingestion reprocesses filings whose normalized chunks changed since
  the previous run
- **THEN** the system updates the corresponding LanceDB rows so runtime
  retrieval uses the latest processed chunk content and segment lineage

## ADDED Requirements

### Requirement: Operator-visible indexing failure diagnostics
The ingestion system SHALL fail with explicit source-aware diagnostics when a
normalized chunk still cannot be embedded after applying the supported
segmentation safeguards.

#### Scenario: Report an unindexable chunk
- **WHEN** a normalized chunk remains too large or otherwise invalid for the
  configured embedding backend after the ingestion system applies its indexing
  segmentation strategy
- **THEN** the ingestion command reports the chunk type, source filing identity,
  processed artifact path, and the supported remediation step instead of only
  surfacing a generic embedding backend error
