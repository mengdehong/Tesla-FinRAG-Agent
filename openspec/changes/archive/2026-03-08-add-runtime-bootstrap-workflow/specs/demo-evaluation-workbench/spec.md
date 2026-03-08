## MODIFIED Requirements

### Requirement: Shared startup failure semantics
The demo and evaluation surfaces SHALL report the same processed-data startup errors and remediation commands as the shared runtime bootstrap, including clear operator guidance for preparing the processed corpus before query execution.

#### Scenario: Workbench starts without processed artifacts
- **WHEN** an operator launches a demo or evaluation surface before processed artifacts exist
- **THEN** the surface reports the missing processed-data prerequisite and shows the supported command to prepare the runtime corpus

#### Scenario: Workbench starts with invalid processed artifacts
- **WHEN** an operator launches a demo or evaluation surface with malformed processed artifacts
- **THEN** the surface reports that the runtime corpus is invalid and directs the operator to rerun the supported ingestion command
