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
The project SHALL maintain an evaluation set of at least five complex Tesla financial questions that cover cross-document comparison, explicit calculation, text-plus-table linkage, or time-sequenced reasoning. The supported evaluation workflow SHALL execute that set against the current processed corpus runtime and persist a baseline run artifact summarizing pass, fail, and error outcomes.

#### Scenario: Run the benchmark set
- **WHEN** an operator runs the evaluation workflow
- **THEN** the system executes at least five predefined complex questions against the current answer pipeline and writes a baseline run artifact that records the resulting summary metrics

### Requirement: Structured failure analysis
The project SHALL maintain at least five structured failure or low-quality answer analyses that record the symptom, root cause hypothesis, an actionable mitigation path, and the benchmark output from which the analysis was derived.

#### Scenario: Record a failed run
- **WHEN** the evaluation workflow identifies an incorrect or low-quality answer
- **THEN** the project stores a failure analysis entry linked to the corresponding benchmark question and baseline run output with enough detail to guide the next improvement cycle

### Requirement: Provider-selectable demo execution
The demo workbench SHALL allow operators to run the same demo corpus through
explicit `local` (Ollama-backed) or `openai-compatible` (remote) provider
modes.

#### Scenario: Run the workbench in local mode
- **WHEN** an operator selects `local` mode in the demo runtime
- **THEN** the workbench executes the provider-aware pipeline through the
  Ollama-backed local provider instead of the old deterministic local path

#### Scenario: Run the workbench in remote mode
- **WHEN** an operator selects `openai-compatible` mode in the demo runtime
- **THEN** the workbench executes the provider-aware pipeline through the
  configured remote provider

### Requirement: Remote mode configuration failure visibility
The demo workbench SHALL fail explicitly when remote mode is requested without
required credentials, without required SOCKS transport support for a detected
proxy configuration, or when the remote provider call fails.

#### Scenario: Remote mode is misconfigured
- **WHEN** an operator requests `openai-compatible` mode without a valid API key
  or the remote provider returns an error
- **THEN** the workbench reports the configuration or provider failure instead
  of silently falling back to local execution

#### Scenario: Remote mode lacks SOCKS transport support
- **WHEN** an operator requests `openai-compatible` mode in an environment that
  routes traffic through a SOCKS proxy and the runtime cannot initialize the
  provider transport correctly
- **THEN** the workbench reports an actionable startup error instead of exposing
  a raw initialization failure

### Requirement: Processed-corpus-backed demo execution
The demo and evaluation surfaces SHALL answer against the processed corpus runtime once processed artifacts are available.

#### Scenario: Run the workbench after ingestion
- **WHEN** an operator launches the workbench or evaluation flow after generating `data/processed`
- **THEN** the system retrieves evidence from the processed corpus instead of a seeded demo fixture corpus

### Requirement: Shared startup failure semantics
The demo and evaluation surfaces SHALL report the same processed-data startup errors and remediation commands as the shared runtime bootstrap, including clear operator guidance for preparing the processed corpus before query execution.

#### Scenario: Workbench starts without processed artifacts
- **WHEN** an operator launches a demo or evaluation surface before processed artifacts exist
- **THEN** the surface reports the missing processed-data prerequisite and shows the supported command to prepare the runtime corpus

#### Scenario: Workbench starts with invalid processed artifacts
- **WHEN** an operator launches a demo or evaluation surface with malformed processed artifacts
- **THEN** the surface reports that the runtime corpus is invalid and directs the operator to rerun the supported ingestion command

### Requirement: Local mode configuration failure visibility
The demo workbench SHALL fail explicitly when local mode is requested without a
reachable Ollama service or without usable local models.

#### Scenario: Local mode cannot start
- **WHEN** an operator requests `local` mode and the local Ollama provider
  cannot be initialized or invoked
- **THEN** the workbench reports the local provider failure instead of silently
  falling back to deterministic execution

### Requirement: Latest baseline discoverability
The evaluation workflow SHALL publish a stable, operator-readable pointer or summary for the latest accepted baseline run so contributors can inspect current benchmark status without manually comparing timestamped run files.

#### Scenario: Inspect the latest baseline
- **WHEN** an operator completes or reviews the evaluation workflow
- **THEN** the repository exposes the latest baseline location and top-line summary metrics in a documented, stable place

### Requirement: Basic LaTeX-aware answer rendering
The demo workbench SHALL render explicit block-math expressions in the answer
body as formatted formulas while continuing to render non-math answer content as
normal text.

#### Scenario: Render block formula in answer text
- **WHEN** the answer text includes an explicit block formula delimited by
  `$$...$$` or `\[...\]`
- **THEN** the workbench displays that block using math rendering instead of raw
  delimiter text

#### Scenario: Keep currency text as plain narrative
- **WHEN** the answer text contains currency expressions such as `$96.77B`
  without explicit block-math delimiters
- **THEN** the workbench keeps that content in the normal text rendering lane
  and does not treat it as a formula block

### Requirement: Non-blocking formula rendering fallback
The demo workbench SHALL preserve answer visibility when a detected formula
block cannot be rendered.

#### Scenario: Formula block is malformed
- **WHEN** a detected formula block contains invalid LaTeX syntax for the
  renderer
- **THEN** the workbench falls back to plain text for that block and continues
  displaying the remainder of the answer, citations, and debug sections

