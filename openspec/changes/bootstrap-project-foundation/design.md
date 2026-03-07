## Context

The Tesla FinRAG repository is effectively at pre-implementation stage: raw filings exist under `data/raw/`, there is one exploratory XBRL download script, and the project has documented product intent and architecture direction but no Python application structure. The project must support multiple later tracks in parallel, including dual-track ingestion, hybrid retrieval, explicit financial calculation, and a Streamlit demo. That makes the foundation change a cross-cutting architectural change rather than a one-off setup task.

## Goals / Non-Goals

**Goals:**

- Create a reproducible Python workspace managed by `uv`.
- Standardize the source tree, test tree, and validation commands.
- Define typed domain contracts that later changes can implement without renaming or reshaping core payloads.
- Establish repository and service interfaces that preserve the intended ingest -> retrieve -> calculate -> answer pipeline.
- Seed the project with basic fixtures and test helpers so downstream tasks can be validated incrementally.

**Non-Goals:**

- Implement actual filing ingestion, indexing, retrieval, or UI features.
- Choose model providers beyond the interface level.
- Build production CI, deployment infrastructure, or remote services.

## Decisions

### Decision: initialize a typed `src/` Python project before feature work

The repository will be bootstrapped as a Python 3.12 project managed by `uv`, with application code under `src/` and tests under `tests/`. This is preferred over ad hoc scripts because every later change needs shared imports, stable tooling, and a lockfile-backed dependency workflow.

Alternative considered: keep building with loose scripts until the ingestion layer is complete. Rejected because it would cause the ingestion change to absorb foundation work, making later parallelization and validation much harder.

### Decision: freeze canonical data models in the foundation phase

The foundation change will introduce typed models for `FilingDocument`, `SectionChunk`, `TableChunk`, `FactRecord`, `QueryPlan`, `EvidenceBundle`, and `AnswerPayload`. Freezing these contracts early keeps later retrieval and UI work from having to renegotiate schemas during implementation.

Alternative considered: define models locally inside each subsystem and merge them later. Rejected because it would create conflicting field names for period handling, citations, and debugging metadata.

### Decision: keep infrastructure behind repository and service interfaces

The project will define repository boundaries for corpus, facts, and retrieval storage, and service boundaries for ingestion orchestration, query planning, calculation, and answer composition. This preserves the repository pattern already favored in the project decisions and leaves room for LanceDB, file-backed, or test-double implementations.

Alternative considered: let later changes bind directly to a chosen backend such as LanceDB. Rejected because it would couple retrieval decisions into the foundation layer and make testing brittle.

### Decision: validation commands are part of the capability

The foundation change will explicitly standardize `uv sync`, `uv run pytest -q`, and `uv run ruff check .` as baseline validation gates. These are not just tooling niceties; they are the operator-facing contract that makes future changes verifiable.

Alternative considered: defer validation commands until a larger feature set exists. Rejected because every subsequent change needs a lightweight completion gate.

## Risks / Trade-offs

- [Risk] The foundation layer could become too abstract before real features land. -> Mitigation: only freeze contracts that are already justified by the research docs and downstream changes.
- [Risk] Shared interfaces may need small field extensions once ingestion starts. -> Mitigation: allow additive field growth, but avoid semantic renames after this change.
- [Risk] Tooling decisions made now could constrain later experimentation. -> Mitigation: use interfaces and settings to isolate provider-specific choices.

## Migration Plan

This change is the starting point for implementation, so there is no existing runtime to migrate. The practical rollout sequence is:

1. Initialize the Python workspace and lock dependencies.
2. Create the source and test package layout.
3. Add typed models, settings, and service/repository contracts.
4. Add baseline fixtures and validation commands.
5. Hand off the stabilized workspace to the ingestion change.

## Open Questions

- Whether the package root should use a top-level name such as `tesla_finrag` or another final import name can be chosen during implementation, but it must be consistent across all later changes.
- If any later change exposes a CLI in addition to Streamlit, the foundation change should only create a minimal launcher pattern rather than a full command suite.
