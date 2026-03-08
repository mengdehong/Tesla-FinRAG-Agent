## ADDED Requirements

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

