## ADDED Requirements

### Requirement: The runtime SHALL execute grounded QA through a bounded repair loop
The system SHALL run plan, retrieval, coverage assessment, and bounded repair attempts before concluding that evidence is insufficient.

#### Scenario: Missing facts trigger a bounded repair
- **WHEN** the initial retrieval is missing required concept-period coverage
- **THEN** the runtime SHALL attempt configured repair actions up to the bounded iteration limit before it halts

### Requirement: The agent SHALL remember prior repairs
The system SHALL track action signatures and SHALL NOT repeat the same repair signature for the same question after it has already failed to add progress.

#### Scenario: Repeated repair would duplicate a failed action
- **WHEN** the next candidate repair would produce an action signature that was already attempted for the question
- **THEN** the agent SHALL skip that repair and SHALL either choose a different action or halt as exhausted
