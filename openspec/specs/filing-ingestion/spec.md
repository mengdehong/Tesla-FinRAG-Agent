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
The ingestion system SHALL normalize narrative sections and tables from filing sources into independent chunk records with source metadata that supports later citations and metadata filtering.

#### Scenario: Normalize a filing section
- **WHEN** the system ingests a filing section such as MD&A or Risk Factors
- **THEN** it emits a narrative chunk with the section path, source document identity, and time metadata

#### Scenario: Preserve a table as its own unit
- **WHEN** the system encounters a financial or operational table in a filing
- **THEN** it emits the table as a standalone chunk instead of merging it into surrounding narrative text

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
The ingestion CLI SHALL report a concise completion summary that includes the processed output location, normalized record counts, and any manifest gaps detected during the run.

#### Scenario: Review ingestion results
- **WHEN** the ingestion CLI completes successfully
- **THEN** the operator can see what artifacts were generated and whether any expected filings were missing
