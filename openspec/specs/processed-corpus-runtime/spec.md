# processed-corpus-runtime Specification

## Purpose
TBD - created by archiving change connect-runtime-to-processed-corpus. Update Purpose after archive.
## Requirements
### Requirement: Processed corpus bootstrap
The runtime SHALL load normalized filings, chunks, and fact records from `data/processed` before executing app, evaluation, or CLI queries.

#### Scenario: Start from processed artifacts
- **WHEN** an operator starts a query surface after the ingestion pipeline has produced processed artifacts
- **THEN** the runtime loads those processed artifacts into the repositories used by planning, retrieval, calculation, and answer assembly

### Requirement: Shared runtime bootstrap across surfaces
The project SHALL use the same processed-corpus bootstrap path for the Streamlit demo, evaluation runner, and package CLI.

#### Scenario: Run different query surfaces
- **WHEN** an operator runs the app, the evaluation workflow, or the package CLI
- **THEN** each surface answers against the same processed runtime rather than separate fixture-specific bootstraps

### Requirement: Explicit processed-data failure
The runtime SHALL fail clearly when required processed artifacts are missing or invalid.

#### Scenario: Processed data is unavailable
- **WHEN** a query surface starts without the required processed artifacts present
- **THEN** the system reports a startup error that tells the operator the processed corpus must be generated first

