# demo-evaluation-workbench Specification

## Purpose
TBD - created by archiving change add-streamlit-evaluation-workbench. Update Purpose after archive.
## Requirements
### Requirement: Inspectable local demo interface
The system SHALL provide a local Streamlit interface that accepts a user question, applies query scope controls, and displays the grounded answer together with citations and debug context.

#### Scenario: Ask a scoped question in the UI
- **WHEN** a user selects a filing scope and submits a question in the Streamlit app
- **THEN** the interface displays the resulting answer payload, including answer text and supporting citations

### Requirement: Debug-aware answer presentation
The demo interface SHALL present calculation steps and retrieval debug information whenever the underlying answer payload includes them.

#### Scenario: Inspect a calculated answer
- **WHEN** the answer pipeline returns calculation steps or retrieval debug fields
- **THEN** the Streamlit interface surfaces those details without requiring the user to inspect backend logs

### Requirement: Complex evaluation set
The project SHALL maintain an evaluation set of at least five complex Tesla financial questions that cover cross-document comparison, explicit calculation, text-plus-table linkage, or time-sequenced reasoning.

#### Scenario: Run the benchmark set
- **WHEN** an operator runs the evaluation workflow
- **THEN** the system executes at least five predefined complex questions against the current answer pipeline

### Requirement: Structured failure analysis
The project SHALL maintain at least five structured failure or low-quality answer analyses that record the symptom, root cause hypothesis, and an actionable mitigation path.

#### Scenario: Record a failed run
- **WHEN** the evaluation workflow identifies an incorrect or low-quality answer
- **THEN** the project stores a failure analysis entry with enough detail to guide the next improvement cycle

### Requirement: Provider-selectable demo execution
The demo workbench SHALL allow operators to run the same demo corpus through explicit `local` or `openai-compatible` provider modes.

#### Scenario: Run the workbench in remote mode
- **WHEN** an operator selects `openai-compatible` mode in the demo runtime
- **THEN** the workbench executes the provider-aware pipeline instead of the local deterministic path

### Requirement: Remote mode configuration failure visibility
The demo workbench SHALL fail explicitly when remote mode is requested without required credentials or when the remote provider call fails.

#### Scenario: Remote mode is misconfigured
- **WHEN** an operator requests `openai-compatible` mode without a valid API key or the remote provider returns an error
- **THEN** the workbench reports the configuration or provider failure instead of silently falling back to local execution

