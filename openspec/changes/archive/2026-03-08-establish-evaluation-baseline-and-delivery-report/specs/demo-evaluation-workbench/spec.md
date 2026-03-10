## MODIFIED Requirements

### Requirement: Complex evaluation set
The project SHALL maintain an evaluation set of at least five complex Tesla financial questions that cover cross-document comparison, explicit calculation, text-plus-table linkage, or time-sequenced reasoning. The supported evaluation workflow SHALL execute that set against the current processed corpus runtime and persist a baseline run artifact summarizing pass, fail, and error outcomes.

#### Scenario: Run the benchmark set
- **WHEN** an operator runs the evaluation workflow
- **THEN** the system executes at least five predefined complex questions against the current answer pipeline and writes a baseline run artifact that records the resulting summary metrics

### Requirement: Structured failure analysis
The project SHALL maintain at least five structured failure or low-quality answer analyses that record the symptom, root cause hypothesis, an actionable mitigation path, and the benchmark output from which the analysis was derived.

#### Scenario: Record a failed run
- **WHEN** the evaluation workflow identifies an incorrect or low-quality answer
- **THEN** the project stores a failure analysis entry linked to the corresponding benchmark question and baseline run output with enough detail to guide the next improvement cycle

## ADDED Requirements

### Requirement: Latest baseline discoverability
The evaluation workflow SHALL publish a stable, operator-readable pointer or summary for the latest accepted baseline run so contributors can inspect current benchmark status without manually comparing timestamped run files.

#### Scenario: Inspect the latest baseline
- **WHEN** an operator completes or reviews the evaluation workflow
- **THEN** the repository exposes the latest baseline location and top-line summary metrics in a documented, stable place
