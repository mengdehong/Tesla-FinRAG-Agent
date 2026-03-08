# ollama-local-demo-pipeline Specification

## Purpose
TBD - created by archiving change switch-local-mode-to-ollama-and-harden-remote-proxy-support. Update Purpose after archive.
## Requirements
### Requirement: Ollama-backed local provider mode
The demo runtime SHALL execute public `local` mode through an Ollama-backed
provider that uses local embeddings and grounded answer narration over the
processed corpus.

#### Scenario: Run a local demo query
- **WHEN** an operator invokes the demo runtime with `provider=local` and a
  reachable Ollama service
- **THEN** the system calls the configured Ollama embedding model for corpus and
  query embeddings and calls the configured Ollama chat model to narrate the
  grounded answer text

### Requirement: Default Ollama local configuration
The demo runtime SHALL provide operator-overrideable defaults for local Ollama
execution, including a default base URL, chat model, embedding model, and
timeout.

#### Scenario: Run local mode without Ollama env overrides
- **WHEN** an operator runs the demo runtime in `local` mode without setting any
  `OLLAMA_*` overrides
- **THEN** the runtime uses the repository defaults for the Ollama endpoint,
  chat model, embedding model, and timeout

### Requirement: Local mode configuration failure visibility
The demo runtime SHALL fail explicitly when `local` mode is requested but the
Ollama service is unavailable or the configured local models cannot be used.

#### Scenario: Local mode is unavailable
- **WHEN** an operator requests `local` mode and the Ollama daemon is not
  reachable or the configured embedding/chat model is unavailable
- **THEN** the system reports the local provider failure and does not fall back
  to a deterministic answer path

