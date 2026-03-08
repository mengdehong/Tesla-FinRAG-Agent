## Context

The codebase already contains the core pieces of the ingestion-to-runtime flow, but they are not exposed as a coherent operator workflow. `src/tesla_finrag/ingestion/pipeline.py` can build `data/processed`, `src/tesla_finrag/runtime.py` can load that corpus, and the Streamlit app plus CLI can query it. The missing piece is an explicit bootstrap path that a developer can follow from a fresh clone without inspecting source code.

Today the package CLI only exposes `ask`, the README stops at environment setup, and runtime failures tell the operator to run ingestion without naming a supported command. That leaves the repository in an awkward state: the “real” chain exists internally, but the externally supported path is incomplete.

This change is cross-cutting because it touches the package CLI, runtime bootstrap errors, Streamlit startup messaging, and developer docs at the same time. It also needs to align with the existing dual-track data model: narrative/table chunks from filings plus normalized structured facts from `companyfacts`.

## Goals / Non-Goals

**Goals:**
- Define a single supported local bootstrap flow from fresh clone to runnable runtime.
- Add a formal ingestion CLI entrypoint that produces the `data/processed` artifact layout already consumed by the runtime.
- Make all runtime surfaces report the same actionable remediation steps when processed artifacts are missing.
- Document the bootstrap and validation path in top-level and developer-focused docs.
- Preserve the existing processed artifact contract and shared workbench pipeline boundary.

**Non-Goals:**
- Auto-running ingestion implicitly when the app or query CLI starts.
- Committing a large processed corpus snapshot as the primary bootstrap path.
- Redesigning ingestion parsing, retrieval, or answer generation logic.
- Introducing a new storage backend or changing the normalized artifact layout under `data/processed`.

## Decisions

### Decision: Promote the package CLI to the operator-facing bootstrap surface

The implementation should add an ingestion subcommand under `python -m tesla_finrag` and make that command the documented way to build `data/processed`. The CLI should wrap the existing `run_pipeline(...)` path, preserve the current output layout, and print a concise summary of what was generated.

This keeps the supported workflow in one place and avoids teaching developers to call internal modules directly.

Alternative considered: ship a standalone script under `scripts/` as the primary interface. Rejected because the package CLI is already the public entrypoint for runtime queries and is a better long-term place for operator commands.

### Decision: Centralize processed-runtime readiness guidance in a shared helper

Runtime startup errors should come from one shared helper that knows how to validate `data/processed` and produce remediation text with concrete next-step commands. The CLI, evaluation runner, and Streamlit app should all rely on that same helper so the operator sees the same guidance everywhere.

Alternative considered: let each surface catch `ProcessedCorpusError` and invent its own message. Rejected because it will drift quickly and produce inconsistent bootstrap instructions.

### Decision: Use explicit commands and documentation instead of a checked-in processed fixture

The mainline workflow should remain:
1. install dependencies,
2. run the ingestion CLI,
3. launch the desired runtime surface.

This change should not make a committed processed snapshot the primary startup path. A checked-in fixture is useful for tests, but as the operator workflow it adds repository weight, obscures provenance, and can mask whether ingestion still works.

Alternative considered: add a minimal fixture processed corpus for developers to copy into place. Rejected as the primary approach because it weakens the repository’s “raw input -> derived artifact” story and risks stale demo data.

### Decision: Keep startup failure fast and actionable, not automatic

If `data/processed` is absent or malformed, the runtime should fail immediately and tell the operator which command to run next, for example `uv run python -m tesla_finrag ingest`. It should not attempt to auto-run ingestion from the app or CLI.

Alternative considered: auto-trigger ingestion on first runtime launch. Rejected because ingestion can be slow, depends on local raw inputs, and would make startup behavior less predictable for both demos and tests.

### Decision: Split user-facing bootstrap docs between README and a dedicated developer doc

The README should contain the shortest happy-path startup chain, while a developer doc should capture extra details such as expected raw inputs, processed output checks, validation commands, and troubleshooting for missing processed artifacts.

Alternative considered: put everything in the README. Rejected because the repository already has a `docs/` area and mixing happy-path startup with all troubleshooting details will make the top-level README harder to scan.

## Risks / Trade-offs

- [Risk] The ingestion CLI may expose pipeline failures that were previously only visible when calling internal modules. -> Mitigation: keep the CLI thin, surface pipeline summaries clearly, and add CLI-focused tests for success and failure cases.
- [Risk] Operators may still try to launch Streamlit first and only then discover missing processed artifacts. -> Mitigation: make the startup error include the exact remediation command and reflect the same command in README quick start.
- [Risk] Different surfaces may still drift if they format readiness errors independently. -> Mitigation: introduce a single helper that returns normalized guidance text consumed by all startup paths.
- [Risk] Choosing not to ship a checked-in processed snapshot means fresh setup still depends on local ingestion time. -> Mitigation: keep the documented flow simple and preserve test fixtures separately for automated validation.

## Migration Plan

1. Add the ingestion CLI subcommand around the existing pipeline runner.
2. Introduce a shared readiness-guidance helper for processed-corpus validation failures.
3. Update package CLI, evaluation runner, and Streamlit app startup paths to use the shared guidance.
4. Update `README.md` and add a developer doc with the formal bootstrap sequence and troubleshooting steps.
5. Add tests covering ingestion CLI behavior, missing-processed guidance, and the documented bootstrap path.

## Open Questions

- None. The change intentionally chooses explicit bootstrap commands over a committed processed fixture as the primary workflow.
