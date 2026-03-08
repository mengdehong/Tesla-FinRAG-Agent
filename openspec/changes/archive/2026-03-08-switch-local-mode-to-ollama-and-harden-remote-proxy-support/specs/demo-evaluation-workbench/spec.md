## ADDED Requirements

### Requirement: Local mode configuration failure visibility
The demo workbench SHALL fail explicitly when local mode is requested without a
reachable Ollama service or without usable local models.

#### Scenario: Local mode cannot start
- **WHEN** an operator requests `local` mode and the local Ollama provider
  cannot be initialized or invoked
- **THEN** the workbench reports the local provider failure instead of silently
  falling back to deterministic execution

## MODIFIED Requirements

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
