# filing-ingestion Specification

## Purpose
TBD - created by archiving change add-dual-track-ingestion-pipeline. Update Purpose after archive.
## Requirements
### Requirement: Filing coverage manifest
The ingestion system SHALL enumerate the target Tesla filing corpus, record source availability for each target filing, and report missing filings as explicit gaps rather than silently omitting them.

#### Scenario: Detect a missing filing
- **WHEN** the target corpus includes a filing that is not present in the local raw sources and cannot be resolved by the ingestion adapter
- **THEN** the system records that filing as a manifest gap with enough identity information for follow-up retrieval

### Requirement: Narrative and table normalization
The ingestion system SHALL normalize narrative sections and tables from filing sources into independent chunk records with source metadata that supports later citations and metadata filtering. For each normalized narrative or table artifact, the system SHALL retain parser provenance, extraction-path metadata, and any available validation status needed to explain how the artifact was produced.

#### Scenario: Normalize a filing section
- **WHEN** the system ingests a filing section such as MD&A or Risk Factors
- **THEN** it emits a narrative chunk with the section path, source document identity, time metadata, and parser provenance sufficient for later debugging

#### Scenario: Preserve a table as its own unit
- **WHEN** the system encounters a financial or operational table in a filing
- **THEN** it emits the table as a standalone chunk instead of merging it into surrounding narrative text, and preserves the caption, page provenance, parser path, and validation metadata needed for later citation review

### Requirement: Structured fact normalization
The ingestion system SHALL normalize XBRL/companyfacts data into typed fact records aligned by metric, unit, filing source, and `period_key` so later calculation steps do not depend on raw PDF extraction.

#### Scenario: Normalize quarterly facts
- **WHEN** the system processes quarterly numeric facts from companyfacts data
- **THEN** it emits fact records that retain the metric identity, numeric value, unit, and aligned period metadata

### Requirement: Immutable raw source handling
The ingestion system SHALL treat files under `data/raw/` as immutable inputs and write normalized or derived ingestion outputs to separate processed locations.

#### Scenario: Persist normalized outputs
- **WHEN** the ingestion system completes a normalization run
- **THEN** it writes processed artifacts outside `data/raw/` so operators can distinguish raw inputs from derived outputs

### Requirement: Operator-facing ingestion CLI
The ingestion system SHALL provide a supported operator-facing CLI command that runs the normalization pipeline from `data/raw/` into the processed artifact layout consumed by the runtime.

#### Scenario: Run ingestion with repository defaults
- **WHEN** an operator invokes the documented ingestion CLI without overriding paths
- **THEN** the system reads the repository raw corpus and writes normalized outputs to the repository processed-corpus location

### Requirement: Ingestion summary reporting
The ingestion CLI SHALL report a concise completion summary that includes the processed output location, LanceDB index status or location, normalized record counts, and any manifest gaps detected during the run.

#### Scenario: Review ingestion results
- **WHEN** the ingestion CLI completes successfully
- **THEN** the operator can see what processed artifacts were generated, whether the LanceDB index was built or refreshed, and whether any expected filings were missing

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

### Requirement: Incremental ingestion reuse
The ingestion system SHALL detect unchanged filing sources and unchanged `companyfacts` input across runs and reuse their existing processed artifacts instead of reparsing them from scratch.

#### Scenario: Reuse unchanged filing artifacts
- **WHEN** an operator reruns ingestion and a filing's raw source plus parser fingerprint are unchanged from the prior successful run
- **THEN** the system reuses that filing's existing processed artifacts rather than reparsing the filing PDF

#### Scenario: Reprocess changed filing artifacts
- **WHEN** an operator reruns ingestion and a filing's raw source fingerprint or parser fingerprint has changed
- **THEN** the system invalidates that filing's prior processed artifacts and regenerates them from the raw source before marking the run successful

#### Scenario: Reuse unchanged fact normalization output
- **WHEN** an operator reruns ingestion and `companyfacts.json` plus the fact-normalization fingerprint are unchanged from the prior successful run
- **THEN** the system reuses the existing processed fact output rather than renormalizing the same fact source

### Requirement: Reuse-aware ingestion reporting
The ingestion CLI SHALL report how many filings were reprocessed, reused, and failed so operators can distinguish actual parsing work from cache hits during a run.

#### Scenario: Review a mixed rerun
- **WHEN** an ingestion run completes after reprocessing some filings and reusing others
- **THEN** the completion summary includes separate counts for reprocessed filings, reused filings, and failed filings

#### Scenario: Review a cold run
- **WHEN** an ingestion run completes without any reusable prior state
- **THEN** the completion summary reports zero reused filings and reflects that the available filings were fully processed

### Requirement: Numeric table validation
The ingestion system SHALL validate extracted financial table cells that appear numeric before treating the resulting table artifact as trusted structured evidence. When authoritative XBRL facts exist for the same concept and period, the ingestion system SHALL reconcile or flag material mismatches instead of silently trusting the parsed table value.

#### Scenario: Accept a valid extracted numeric cell
- **WHEN** an extracted table cell can be normalized as a numeric value and does not materially conflict with an authoritative fact for the same concept and period
- **THEN** the system records the normalized value as validated table evidence for downstream retrieval and citations

#### Scenario: Flag a malformed or conflicting numeric cell
- **WHEN** an extracted table cell cannot be normalized as a numeric value or materially conflicts with an authoritative fact after unit and scale alignment
- **THEN** the system marks the affected table output as validation-failed and records a source-aware diagnostic instead of silently treating the value as trusted evidence

### Requirement: Source-aware parser diagnostics
The ingestion system SHALL record which parser path produced each normalized filing artifact and SHALL surface actionable diagnostics when extraction fails or falls back to a lower-confidence parser path.

#### Scenario: Fall back from the primary parser
- **WHEN** the primary text or table extraction path fails or yields unusable structured output for a filing artifact
- **THEN** the system records the fallback parser path together with the filing and page identity needed for operator review

#### Scenario: Report an extraction failure
- **WHEN** no supported parser path can produce valid normalized output for a required filing artifact
- **THEN** the ingestion run reports the filing identity, artifact type, parser attempts, and remediation guidance in its diagnostics

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

