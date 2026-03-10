## MODIFIED Requirements

### Requirement: Explicit processed-data failure
The runtime SHALL fail clearly when required processed artifacts are missing or invalid, and the failure SHALL include actionable remediation guidance that points to the supported command for generating or regenerating the processed corpus.

#### Scenario: Processed data is unavailable
- **WHEN** a query surface starts without the required processed artifacts present
- **THEN** the system reports a startup error that tells the operator the processed corpus must be generated first and includes the supported next-step command

#### Scenario: Processed data is malformed
- **WHEN** a query surface starts with processed artifacts that exist but do not match the expected runtime schema
- **THEN** the system reports that the processed corpus is invalid and instructs the operator to rerun the supported ingestion command

