## 1. Provider and configuration setup

- [x] 1.1 Add the `openai` SDK dependency and extend settings with `OPENAI_BASE_URL` and `OPENAI_TIMEOUT_SECONDS`.
- [x] 1.2 Implement an OpenAI-compatible provider wrapper for embeddings and grounded answer narration.

## 2. Demo runtime and CLI

- [x] 2.1 Extend `WorkbenchPipeline` with explicit `local` and `openai-compatible` provider modes over the seeded demo corpus.
- [x] 2.2 Add a package `ask` CLI command with `--provider` and `--json` support.
- [x] 2.3 Extend `AnswerPayload.retrieval_debug` generation with provider, model, and vector-lane diagnostics.

## 3. Validation

- [x] 3.1 Add tests for settings loading, provider request wiring, and fake-client remote execution.
- [x] 3.2 Add runtime and CLI smoke tests covering `local` success and explicit remote-mode failure on missing credentials.
