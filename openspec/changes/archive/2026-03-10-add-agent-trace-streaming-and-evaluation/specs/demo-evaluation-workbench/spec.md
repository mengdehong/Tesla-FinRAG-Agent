## ADDED Requirements

### Requirement: The backend SHALL expose a streaming-friendly run path
The workbench backend SHALL provide an additive generator-style run path that emits typed agent progress events for future UI and evaluation consumers.

#### Scenario: Backend consumer subscribes to progress
- **WHEN** a caller uses the streaming backend path for a question
- **THEN** the backend SHALL emit typed events for plan creation, retrieval, coverage assessment, repair selection/completion, answer completion, and final halt
