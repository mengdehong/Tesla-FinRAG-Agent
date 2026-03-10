## ADDED Requirements

### Requirement: The planner SHALL support structured LLM parsing with rule fallback
The system SHALL support an LLM-first query planner that emits a typed planning result and SHALL fall back to the rule-based planner when the structured result is missing, malformed, or below the configured confidence threshold.

#### Scenario: Low-confidence structured plan
- **WHEN** the LLM returns a planner confidence below the configured minimum
- **THEN** the final `QueryPlan` SHALL be produced by the rule fallback path and SHALL record that fallback in planner diagnostics

#### Scenario: Successful structured plan
- **WHEN** the LLM returns a valid structured plan above the configured threshold
- **THEN** the final `QueryPlan` SHALL preserve typed planner diagnostics including planner mode, planner confidence, and resolved metric mentions
