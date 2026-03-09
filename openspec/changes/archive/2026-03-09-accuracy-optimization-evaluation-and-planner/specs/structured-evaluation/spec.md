## ADDED Requirements

### Requirement: Structured assertion judge for benchmark questions

The evaluation system SHALL support a structured assertion judge that validates answers using typed assertions (status check, fact hit check, numeric tolerance check) instead of keyword substring matching. The structured judge SHALL be the primary evaluation metric.

#### Scenario: Numeric value within tolerance passes

- **WHEN** a benchmark question has `expected_calc` with `operation: "lookup"`, `expected_value: 96773000000`, and `tolerance: 0.001`
- **THEN** the structured judge SHALL mark the question as passed if the answer status is `ok` AND the answer text contains a numeric value within 0.1% of the expected value

#### Scenario: Percentage change within tolerance passes

- **WHEN** a benchmark question has `expected_calc` with `operation: "pct_change"`, `expected_value: 18.80`, and `tolerance: 0.01`
- **THEN** the structured judge SHALL mark the question as passed if the answer status is `ok` AND the calculation trace or answer text contains a value within 1% relative tolerance of 18.80

#### Scenario: Fact hit assertion

- **WHEN** a benchmark question has `expected_facts: ["us-gaap:Revenues"]`
- **THEN** the structured judge SHALL verify that the answer's retrieval debug data or calculation trace references at least one fact matching the expected concept

#### Scenario: Status assertion

- **WHEN** a benchmark question has `expected_status: "ok"`
- **THEN** the structured judge SHALL verify that the answer status equals `ok`, independent of answer text content

### Requirement: Legacy keyword judge preserved as secondary metric

The evaluation system SHALL retain the existing keyword-contains judge and record its result separately from the structured judge. The legacy result SHALL be stored in `QuestionResult.legacy_passed`.

#### Scenario: Legacy and structured judges produce independent results

- **WHEN** an answer contains the correct numeric value but does not contain a legacy keyword like "result"
- **THEN** the structured judge SHALL pass AND the legacy judge SHALL fail, with both results recorded independently

### Requirement: Judge breakdown diagnostic output

Each benchmark question result SHALL include a `judge_breakdown` field containing: `status_ok` (bool), `facts_found` (list), `facts_missing` (list), `calc_correct` (bool or null), and `calc_detail` (string).

#### Scenario: Breakdown shows specific missing facts

- **WHEN** a benchmark question expects facts `["us-gaap:GrossProfit", "us-gaap:Revenues"]` but only `us-gaap:Revenues` was found
- **THEN** the judge breakdown SHALL report `facts_found: ["us-gaap:Revenues"]` and `facts_missing: ["us-gaap:GrossProfit"]`

### Requirement: Benchmark question schema supports structured assertions

The `BenchmarkQuestion` model SHALL accept optional fields: `expected_status` (AnswerStatus), `expected_facts` (list of concept strings), `expected_calc` (object with operation, expected_value, tolerance), and `expected_period_semantics` (dict). All fields SHALL have defaults that preserve backward compatibility with existing question definitions.

#### Scenario: Existing questions without structured fields remain valid

- **WHEN** a benchmark question JSON has only the original fields (`question_id`, `question`, `category`, `difficulty`, `expected_answer_contains`, `required_periods`, `required_concepts`)
- **THEN** the question SHALL load successfully with structured fields defaulting to None/empty, and the legacy judge SHALL still operate normally

### Requirement: Extended question result with dual-track pass/fail

The `QuestionResult` model SHALL include `legacy_passed` (bool or null), `structured_passed` (bool or null), and `judge_breakdown` (JudgeBreakdown or null). The `passed` field SHALL be determined by `structured_passed` when available, falling back to legacy judge when structured fields are absent.

#### Scenario: Passed field follows structured judge

- **WHEN** structured_passed is True and legacy_passed is False
- **THEN** the `passed` field SHALL be True
