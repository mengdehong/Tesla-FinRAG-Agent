## MODIFIED Requirements

### Requirement: Processed corpus bootstrap
The runtime SHALL load normalized filings, chunks, and fact records from `data/processed` and SHALL open the processed LanceDB vector index before executing app, evaluation, or CLI queries.

#### Scenario: Start from processed artifacts
- **WHEN** an operator starts a query surface after the ingestion pipeline has produced processed artifacts and the LanceDB index
- **THEN** the runtime loads those processed artifacts and connects to the persisted LanceDB retrieval store used by planning, retrieval, calculation, and answer assembly

### Requirement: Explicit processed-data failure
The runtime SHALL fail clearly when required processed artifacts or the required LanceDB index are missing or invalid, and the failure SHALL include actionable remediation guidance that points to the supported command for generating or regenerating the processed corpus.

#### Scenario: Processed data is unavailable
- **WHEN** a query surface starts without the required processed artifacts or LanceDB index present
- **THEN** the system reports a startup error that tells the operator the processed corpus must be generated first and includes the supported next-step command

#### Scenario: Processed data is malformed
- **WHEN** a query surface starts with processed artifacts or a LanceDB index that exist but do not match the expected runtime schema or embedding configuration
- **THEN** the system reports that the runtime corpus is invalid and instructs the operator to rerun the supported ingestion command
