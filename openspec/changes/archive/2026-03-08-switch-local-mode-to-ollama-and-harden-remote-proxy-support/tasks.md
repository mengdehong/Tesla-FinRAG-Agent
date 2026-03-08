## 1. Provider and configuration setup

- [x] 1.1 Add SOCKS-capable remote transport support and extend settings with the required `OLLAMA_*` fields plus defaults.
- [x] 1.2 Implement `OllamaProvider` and normalize provider initialization/request failures into actionable `ProviderError` diagnostics.

## 2. Runtime execution changes

- [x] 2.1 Refactor the workbench pipeline so public `local` mode uses the Ollama-backed provider path while `openai-compatible` remains the explicit remote mode.
- [x] 2.2 Update retrieval debug metadata and runtime failure handling for local Ollama execution and remote SOCKS-sensitive startup.

## 3. User-facing surfaces and docs

- [x] 3.1 Update the Streamlit app and package CLI help/output to describe `local (Ollama)` and `remote (OpenAI-compatible)` behavior without breaking existing provider argument values.
- [x] 3.2 Update `.env.example` and operator-facing docs with Ollama defaults, required model pulls, and remote proxy expectations.

## 4. Validation

- [x] 4.1 Add or update tests for settings, provider wiring, workbench runtime behavior, and CLI/local-vs-remote failure semantics.
- [x] 4.2 Run targeted validation for the changed OpenSpec artifacts and Python test coverage, then mark the change ready to archive.
