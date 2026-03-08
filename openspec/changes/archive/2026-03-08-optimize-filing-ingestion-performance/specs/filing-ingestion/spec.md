## ADDED Requirements

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
