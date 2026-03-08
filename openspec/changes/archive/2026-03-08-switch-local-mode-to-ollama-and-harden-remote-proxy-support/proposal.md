## Why

The current workbench exposes a `local` mode that is still deterministic and a
remote `openai-compatible` mode that can fail to initialize in SOCKS proxy
environments because the runtime does not guarantee `httpx` SOCKS support. This
blocks the intended local-vs-remote demo workflow and makes the provider mode
labels diverge from the actual execution path.

## What Changes

- Replace the public `local` demo/workbench mode with an Ollama-backed provider
  path that uses local embedding and local grounded answer narration.
- Keep the public remote mode on `openai-compatible`, but harden it for SOCKS
  proxy environments and surface actionable initialization errors.
- Refactor the provider-aware workbench pipeline so both public modes run
  through the same provider-backed retrieval and grounded-answer flow.
- Update the Streamlit app, CLI, environment template, and operator docs to
  describe `local (Ollama)` and `remote (OpenAI-compatible)` execution clearly.
- Expand validation coverage for Ollama provider wiring, provider diagnostics,
  explicit local/remote failure modes, and proxy-sensitive remote startup.

## Capabilities

### New Capabilities
- `ollama-local-demo-pipeline`: Run the processed-corpus demo pipeline in public
  `local` mode through an Ollama-backed embedding and grounded-answer provider.

### Modified Capabilities
- `demo-evaluation-workbench`: The workbench provider selection, labels, and
  startup/runtime failure semantics change to reflect Ollama-backed local
  execution and explicit remote provider diagnostics.
- `openai-compatible-demo-pipeline`: The remote provider contract adds SOCKS
  proxy-safe startup requirements and clearer initialization failure behavior.

## Impact

- Affected code: settings, provider modules, workbench runtime wiring, CLI, app
  labels, test suites, and operator-facing docs.
- Affected APIs: `ProviderMode` semantics for `local`, environment variables for
  local provider configuration, and `AnswerPayload.retrieval_debug` provider
  metadata.
- Dependencies: add direct SOCKS-capable `httpx` runtime support.
- Systems: Streamlit demo, package CLI, local Ollama execution, and remote
  OpenAI-compatible startup under proxy environments.
