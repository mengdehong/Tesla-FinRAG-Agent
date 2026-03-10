## Context

The repository now has a usable local demo/workbench pipeline, but that path is entirely deterministic and corpus-seeded. `AppSettings` already contains core OpenAI model names, and local `.env` usage already expects `OPENAI_*` variables, yet there is no supported remote provider layer, no vector-lane execution against a remote embedding API, and no CLI smoke command for validating an end-to-end remote demo path.

This change is intentionally scoped to the existing demo corpus and demo runtime. It does not switch the application to `data/processed`, rerun ingestion, or broaden the evaluation benchmark contract.

## Goals / Non-Goals

**Goals:**
- Add an explicit `openai-compatible` provider mode alongside the existing `local` mode.
- Use the official `openai` SDK with `base_url` compatibility for both embeddings and chat.
- Build a real in-memory vector index for the demo corpus when remote mode is selected.
- Keep citations, calculation traces, confidence, and answer status under local deterministic control.
- Add a package CLI smoke path that exercises the same runtime used by the demo.

**Non-Goals:**
- Reading from `data/processed` or replacing the seeded demo corpus.
- Re-running ingestion or adding new storage backends.
- Introducing generic multi-provider orchestration frameworks.
- Allowing silent fallback from remote mode to local mode.

## Decisions

### Decision: Use a thin OpenAI-compatible provider wrapper over the official SDK
The implementation will add a small provider module that exposes `embed_texts()` and `generate_grounded_answer()` while reading configuration from `AppSettings`. This keeps the dependency surface narrow and matches the repository's preference for typed service modules over generic orchestration layers.

Alternative considered: add LiteLLM or LangChain as a provider abstraction. Rejected because the change only needs one OpenAI-compatible transport and does not justify extra framework complexity.

### Decision: Keep provider mode selection at the workbench runtime boundary
`WorkbenchPipeline` will accept an explicit provider mode and reuse the same local planning, retrieval assembly, citation generation, and calculation logic. The remote path will only replace the embedding source and natural-language answer narration.

Alternative considered: add separate local and remote pipelines. Rejected because it would duplicate query handling and make app/CLI behavior drift.

### Decision: Remote mode builds an in-memory vector index from the demo corpus
When `openai-compatible` mode is requested, the runtime will embed section/table chunks from the seeded demo corpus and load them into the existing in-memory retrieval store. Query execution will embed the question with the same provider so hybrid retrieval actually exercises the vector lane.

Alternative considered: keep remote mode chat-only and leave retrieval lexical-only. Rejected because that would not validate the embedding contract or vector diagnostics.

### Decision: Remote chat only narrates grounded evidence
The remote chat call will receive the already-grounded query plan, evidence summary, and calculation trace, and will only produce `answer_text`. `AnswerStatus`, citations, confidence, and calculation trace remain locally computed so the system preserves the grounding contract.

Alternative considered: let the chat model produce the entire answer payload. Rejected because it weakens traceability and makes regression behavior harder to test.

### Decision: Add a package-level `ask` command instead of a separate script
The repository CLI will gain an `ask` subcommand with `--provider local|openai-compatible` and optional `--json`. This keeps smoke validation inside the same entrypoint that already owns repository-wide developer commands.

Alternative considered: add a standalone script under `scripts/`. Rejected because CLI behavior is part of the developer-facing contract for this change.

## Risks / Trade-offs

- [Risk] Remote embeddings are slower than the current local deterministic path. -> Mitigation: cache the built remote index inside the process and make local the default mode.
- [Risk] OpenAI-compatible providers differ subtly in response shape or limits. -> Mitigation: keep the wrapper narrow, use official SDK request shapes, and add fake-client tests for the exact calls.
- [Risk] Remote chat may hallucinate beyond the provided evidence. -> Mitigation: constrain the chat prompt to evidence-only narration and preserve local status/citation/calculation fields.
- [Risk] Missing credentials or provider outages can make smoke runs flaky. -> Mitigation: fail fast with explicit configuration or transport errors and keep remote mode opt-in.

## Migration Plan

1. Add the `openai` SDK and extend `AppSettings` with `OPENAI_BASE_URL` and `OPENAI_TIMEOUT_SECONDS`.
2. Implement the provider wrapper and provider-aware workbench runtime.
3. Extend the package CLI with `ask` and provider selection.
4. Add tests for settings, provider calls, runtime vector-lane execution, and CLI behavior.
5. Validate `local` mode remains the default and that `openai-compatible` mode produces provider diagnostics.

## Open Questions

- None. This change intentionally fixes provider scope to a single OpenAI-compatible implementation and a non-streaming sync client.
