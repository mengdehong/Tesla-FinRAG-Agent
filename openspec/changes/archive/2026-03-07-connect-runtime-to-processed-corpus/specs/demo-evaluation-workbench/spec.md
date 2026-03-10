## ADDED Requirements

### Requirement: Processed-corpus-backed demo execution
The demo and evaluation surfaces SHALL answer against the processed corpus runtime once processed artifacts are available.

#### Scenario: Run the workbench after ingestion
- **WHEN** an operator launches the workbench or evaluation flow after generating `data/processed`
- **THEN** the system retrieves evidence from the processed corpus instead of a seeded demo fixture corpus

### Requirement: Shared startup failure semantics
The demo and evaluation surfaces SHALL report the same processed-data startup errors as the shared runtime bootstrap.

#### Scenario: Workbench starts without processed artifacts
- **WHEN** an operator launches a demo or evaluation surface before processed artifacts exist
- **THEN** the surface reports the missing processed-data prerequisite instead of answering from a fallback corpus
