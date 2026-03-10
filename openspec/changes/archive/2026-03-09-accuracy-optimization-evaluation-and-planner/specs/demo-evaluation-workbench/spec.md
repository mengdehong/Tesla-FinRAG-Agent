## MODIFIED Requirements

### Requirement: Evaluation runner supports dual-track judging

The evaluation runner SHALL execute both the legacy keyword judge and the structured assertion judge for each benchmark question, recording both results independently. The primary `passed` field SHALL be determined by the structured judge when structured assertion fields are present on the benchmark question, falling back to the legacy judge otherwise.

#### Scenario: Dual-track execution for a question with both field sets

- **WHEN** a benchmark question has both `expected_answer_contains` and `expected_calc` fields
- **THEN** the runner SHALL execute both judges and populate `legacy_passed`, `structured_passed`, and `judge_breakdown` on the result

#### Scenario: Legacy-only fallback for questions without structured fields

- **WHEN** a benchmark question has only `expected_answer_contains` and no `expected_status`, `expected_facts`, or `expected_calc`
- **THEN** the runner SHALL use the legacy judge for `passed` and set `structured_passed` to None

### Requirement: Evaluation run saves comprehensive debug data

Each question result in an evaluation run SHALL include the full retrieval debug payload from the answer (query plan metadata, retrieved facts count, calculation trace, limitation reasons) so that failure diagnosis does not require re-running the pipeline.

#### Scenario: Debug data available in saved run file

- **WHEN** an evaluation run is saved to disk
- **THEN** each question result SHALL contain retrieval debug data including `query_type`, `required_periods`, `required_concepts`, `limitation_reasons`, and `missing_periods`
