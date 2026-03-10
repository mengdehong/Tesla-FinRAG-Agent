## MODIFIED Requirements

### Requirement: Narrative and table normalization
The ingestion system SHALL normalize narrative sections and tables from filing sources into independent chunk records with source metadata that supports later citations and metadata filtering. For each normalized narrative or table artifact, the system SHALL retain parser provenance, extraction-path metadata, and any available validation status needed to explain how the artifact was produced.

#### Scenario: Normalize a filing section
- **WHEN** the system ingests a filing section such as MD&A or Risk Factors
- **THEN** it emits a narrative chunk with the section path, source document identity, time metadata, and parser provenance sufficient for later debugging

#### Scenario: Preserve a table as its own unit
- **WHEN** the system encounters a financial or operational table in a filing
- **THEN** it emits the table as a standalone chunk instead of merging it into surrounding narrative text, and preserves the caption, page provenance, parser path, and validation metadata needed for later citation review

## ADDED Requirements

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
