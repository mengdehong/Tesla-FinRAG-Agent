## ADDED Requirements

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
