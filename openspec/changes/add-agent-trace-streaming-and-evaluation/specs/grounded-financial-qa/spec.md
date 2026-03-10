## ADDED Requirements

### Requirement: Final answers SHALL expose structured agent diagnostics
The system SHALL include additive agent diagnostics in final answer metadata so evaluation and operators can inspect halt reasons, attempted actions, and iteration traces.

#### Scenario: Agent finishes with bounded retries
- **WHEN** the agent completes a question
- **THEN** the final answer metadata SHALL include the halt reason and a structured summary of the iterations that were executed
