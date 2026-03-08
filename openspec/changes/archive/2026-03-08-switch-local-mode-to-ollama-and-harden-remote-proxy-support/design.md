## Context

The repository already has a processed-corpus-backed workbench pipeline and an
OpenAI-compatible remote provider path, but the public provider semantics no
longer match the intended operator workflow. `local` still means
template-driven deterministic execution, while `openai-compatible` is the only
provider-backed path. At the same time, remote startup can fail before the
pipeline is built when the environment routes traffic through a SOCKS proxy and
the runtime lacks `httpx` SOCKS support.

This change affects multiple layers together: settings, provider wiring, the
workbench runtime, Streamlit labels, CLI help, docs, and tests. It also changes
public behavior, so the OpenSpec contract must be explicit about what `local`
and `openai-compatible` mean after the change.

## Goals / Non-Goals

**Goals:**
- Make public `local` mode execute through Ollama for both embeddings and
  grounded answer narration.
- Keep `openai-compatible` as the public remote mode while making its startup
  safe in SOCKS proxy environments.
- Reuse one provider-backed retrieval and answer flow for both public modes so
  diagnostics, scope handling, and grounding behavior stay aligned.
- Preserve backward compatibility for CLI argument values while updating user
  facing labels and docs.
- Add deterministic, mockable tests for both local and remote provider wiring
  without introducing live network dependencies into CI.

**Non-Goals:**
- Adding a third public provider mode.
- Replacing the processed corpus runtime, planner, retrieval contracts, or
  answer payload shape.
- Introducing streaming responses, async provider execution, or generic agent
  orchestration frameworks.
- Silently falling back from public `local` or `openai-compatible` to the old
  deterministic demo path.

## Decisions

### Decision: Split provider implementations, but unify the runtime contract
The runtime will keep a small shared provider contract with `embed_texts()`,
`generate_grounded_answer()`, and diagnostic metadata. Two concrete
implementations will satisfy that contract: `OpenAIProvider` for remote mode and
`OllamaProvider` for local mode.

Alternative considered: reuse `OpenAIProvider` for Ollama by passing an Ollama
base URL. Rejected because the public semantics, configuration rules, and error
messages are different enough that one class would blur local-vs-remote intent.

### Decision: Keep public provider enum values stable
`ProviderMode` will remain `local` and `openai-compatible`. `local` changes
meaning from deterministic execution to Ollama-backed execution, while
`openai-compatible` remains the explicit remote path. Streamlit labels and CLI
help text will explain the new behavior, but the raw value names stay stable so
existing scripts do not break.

Alternative considered: rename the remote mode to `remote` or add a new public
`ollama` mode. Rejected because it would force avoidable CLI and UI contract
changes without adding capability.

### Decision: Ollama uses the OpenAI-compatible endpoint with explicit defaults
`OllamaProvider` will use the official `openai` SDK against
`http://localhost:11434/v1`, with defaults
`qwen2.5:7b-instruct` for chat and `nomic-embed-text` for embeddings. The SDK
will receive a fixed placeholder API key (`ollama`) so the local path does not
require extra credentials.

Alternative considered: use Ollama's native REST API or Python client.
Rejected because the repository already has tested OpenAI SDK request wiring,
and reusing that transport keeps the local provider thin and easy to fake in
tests.

### Decision: Public local mode is provider-backed only
`WorkbenchPipeline` will use the same provider-backed vector-index and grounded
answer narration flow for both public modes. The existing deterministic answer
composer remains as an internal grounding helper for citations, status, and
calculation traces, but not as a selectable public provider mode.

Alternative considered: keep the old deterministic path as a silent fallback
when Ollama is unavailable. Rejected because the user explicitly asked to change
local mode to Ollama and silent fallback would hide real environment problems.

### Decision: Fix SOCKS startup through dependency + error normalization
The runtime will add a direct `httpx[socks]` dependency and normalize provider
construction failures into `ProviderError`. When the exception chain indicates a
SOCKS support problem, the surfaced message will explain the missing SOCKS
transport requirement instead of exposing a raw SDK trace.

Alternative considered: only document the dependency or only catch the error.
Rejected because dependency-only leaves poor diagnostics for broken environments
and error-only still leaves the runtime under-specified.

### Decision: Tests must stay offline and deterministic
Provider-facing tests will use fake clients and injected providers. Existing CLI
tests that currently assume `local` is network-free will be updated so they no
longer require a real Ollama server in CI. Subprocess coverage remains for
argument/help and processed-corpus failure cases, while provider-backed success
paths move to injectable direct-call tests where needed.

Alternative considered: require a live Ollama daemon in CI. Rejected because it
adds environment fragility without improving contract coverage.

## Risks / Trade-offs

- [Risk] Local mode is no longer zero-setup; users now need a running Ollama
  daemon and pulled models. -> Mitigation: provide explicit defaults, startup
  checks, and operator guidance in `.env.example`, README, and UI/CLI errors.
- [Risk] Remote SOCKS failures may still come from external proxy
  misconfiguration rather than dependency state. -> Mitigation: normalize the
  error message and preserve the underlying cause in logs.
- [Risk] Keeping `openai-compatible` as the public remote enum while `local`
  also uses an OpenAI-compatible transport may confuse maintainers. -> Mitigation:
  keep transport details private and expose provider diagnostics that name the
  actual provider (`ollama` vs `openai-compatible`).
- [Risk] Moving local mode to provider-backed retrieval changes prior local test
  assumptions. -> Mitigation: update the tests around provider injection and
  keep the grounding helper deterministic underneath the narrated answer text.

## Migration Plan

1. Create the new OpenSpec delta specs and tasks for local Ollama behavior,
   workbench mode semantics, and remote SOCKS-safe startup.
2. Add settings and provider abstractions, including `OllamaProvider` and direct
   `httpx[socks]` support.
3. Refactor the workbench runtime, Streamlit app, and CLI to use the new local
   and remote behavior.
4. Update docs and env templates with Ollama prerequisites and remote proxy
   expectations.
5. Update and run targeted tests for provider wiring, runtime execution, and CLI
   failure semantics.

## Open Questions

- None. This change fixes the public mode semantics, default Ollama model pair,
  and remote dependency strategy explicitly.
