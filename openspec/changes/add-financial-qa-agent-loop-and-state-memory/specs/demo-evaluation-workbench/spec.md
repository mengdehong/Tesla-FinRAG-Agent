## ADDED Requirements

### Requirement: Workbench callers SHALL keep the current run contract
The demo/evaluation backend SHALL continue to return `(plan, bundle, answer)` even after moving to the bounded agent runtime.

#### Scenario: Existing backend caller runs a question
- **WHEN** a caller invokes the workbench pipeline run path
- **THEN** the caller SHALL still receive the same three-part return contract while additive agent diagnostics are attached to the answer metadata
