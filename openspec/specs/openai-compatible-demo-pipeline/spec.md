# openai-compatible-demo-pipeline Specification

## Purpose
TBD - created by archiving change add-openai-compatible-demo-pipeline. Update Purpose after archive.
## Requirements
### Requirement: Explicit remote provider mode
The demo runtime SHALL support an explicit `openai-compatible` provider mode
that uses configured OpenAI-compatible APIs for embeddings and grounded answer
narration over the processed corpus runtime.

#### Scenario: Run a remote demo query
- **WHEN** an operator invokes the demo runtime with `provider=openai-compatible`
  and valid provider credentials
- **THEN** the system calls the configured embedding model for corpus and query
  embeddings and calls the configured chat model to narrate the grounded answer
  text

### Requirement: Explicit local default mode
The demo runtime SHALL keep `local` as the default provider mode and SHALL
execute that mode through the configured Ollama-backed local provider unless the
operator explicitly selects `openai-compatible`.

#### Scenario: Run the default local path
- **WHEN** an operator runs the demo runtime without selecting a remote provider
  mode
- **THEN** the system answers using the Ollama-backed local path and does not
  call the remote OpenAI-compatible provider

### Requirement: CLI smoke execution
The project SHALL provide a CLI question-answering command for the demo runtime that supports concise text output and full JSON output.

#### Scenario: Ask a question from the package CLI
- **WHEN** an operator runs `python -m tesla_finrag ask --question "..."`
- **THEN** the command prints the answer summary by default and can emit the full `AnswerPayload` when `--json` is requested

### Requirement: Provider-aware diagnostics
The answer payload SHALL report provider and vector-lane diagnostics whenever the demo runtime executes in either provider mode.

#### Scenario: Inspect remote execution metadata
- **WHEN** a question is answered through the demo runtime
- **THEN** `AnswerPayload.retrieval_debug` includes the answer provider, the shared indexed embedding backend, selected models, and vector hit counts for that run

### Requirement: SOCKS proxy-compatible remote startup
The demo runtime SHALL initialize the configured `openai-compatible` provider in
supported SOCKS proxy environments without requiring extra manual dependency
installation after the project dependencies are synced.

#### Scenario: Start remote mode with SOCKS proxy settings
- **WHEN** an operator launches the demo runtime in `openai-compatible` mode in
  an environment with SOCKS proxy variables configured
- **THEN** the runtime initializes the provider transport without failing due to
  missing SOCKS support packages

