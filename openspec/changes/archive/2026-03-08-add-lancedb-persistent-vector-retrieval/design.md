## Context

The repository already models retrieval storage as a replaceable repository boundary and documents LanceDB as the preferred local-first backend, but the current implementation stops at in-memory storage. `ingest` writes normalized filing artifacts to `data/processed/`, then each runtime surface reloads those files, re-embeds every chunk through the active provider, and builds an `InMemoryRetrievalStore` inside the workbench pipeline.

That gap causes three problems. First, the project never actually writes retrieval data into a database, so the documented LanceDB choice is not observable in the product. Second, startup cost grows with the processed corpus because every process rebuilds the vector lane from scratch. Third, provider mode currently couples answer narration and embeddings, which becomes incompatible with a single persistent vector index unless the embedding space is stabilized.

## Goals / Non-Goals

**Goals:**
- Persist section and table chunk embeddings plus retrieval metadata in a file-backed LanceDB database under `data/processed/lancedb`.
- Build or refresh that database during `ingest` so runtime surfaces can treat it as a prepared artifact rather than a startup side effect.
- Replace the in-memory vector lane with a LanceDB-backed retrieval store while keeping the existing typed repository and hybrid retrieval boundaries.
- Keep `data/processed` JSON and JSONL artifacts as the source of truth for filing metadata, chunk payloads, and facts.
- Make provider modes share one embedding space for indexed retrieval while preserving provider-specific answer narration.
- Report missing, stale, or invalid LanceDB state through the same operator-facing readiness guidance used by app, CLI, and evaluation entrypoints.

**Non-Goals:**
- Move `FactRecord` or filing metadata storage into LanceDB in this change.
- Replace lexical search, structured fact retrieval, or answer composition.
- Introduce a remote or multi-tenant database deployment model.
- Preserve the current behavior where each provider mode can index the corpus with a different embedding model.

## Decisions

### Decision: Treat LanceDB as a persisted retrieval sidecar to `data/processed`

The processed JSON and JSONL artifacts remain the canonical normalized corpus. LanceDB is added as a derived retrieval artifact rooted at `data/processed/lancedb`, storing chunk identifiers, document identifiers, chunk kind, key metadata filters, display text used for embeddings, and the embedding vector itself.

This keeps the current runtime bootstrap and facts loading model intact while solving the missing persistence problem for semantic retrieval.

Alternative considered: move all corpus and fact storage into LanceDB. Rejected because it expands the migration surface, changes too many existing runtime contracts at once, and is outside the user-confirmed scope for this proposal.

### Decision: Build and refresh the LanceDB index inside `ingest`

`python -m tesla_finrag ingest` becomes responsible for producing a retrieval-ready processed corpus: normalized files plus a synchronized LanceDB index. The ingestion flow will derive embeddings for section and table chunks after normalization, then upsert LanceDB rows keyed by stable chunk IDs. Re-runs overwrite or replace stale rows for changed chunks and can clear rows for removed filings.

This preserves a single preparation command, avoids runtime write side effects, and gives operators a clear answer to whether data is written into the database.

Alternative considered: lazily build the database on first runtime query. Rejected because it hides expensive writes behind interactive startup, complicates failure semantics, and makes runtime behavior depend on mutable local state.

### Decision: Use one shared embedding backend for indexing and query-time vector search

The project will define one indexing embedding configuration for LanceDB and use that same backend both when `ingest` builds embeddings and when runtime computes query embeddings. Provider mode remains relevant for answer narration, but it no longer changes the corpus embedding space. Retrieval debug metadata will expose both the indexed embedding backend and the selected answer provider so operators can see the split explicitly.

This preserves one consistent LanceDB index across local and `openai-compatible` modes.

Alternative considered: maintain separate LanceDB indexes per provider mode. Rejected because it multiplies storage, complicates ingestion prerequisites, and forces operators to keep multiple corpora in sync for little product value at this stage.

### Decision: Keep retrieval integration behind the existing `RetrievalStore` boundary

A concrete LanceDB retrieval store will implement the existing `RetrievalStore` contract so `HybridRetrievalService` and higher layers do not need backend-specific branching. The workbench pipeline will stop building `InMemoryRetrievalStore` instances and instead receive a ready-to-use LanceDB-backed store during bootstrap.

This limits blast radius and preserves the ability to test hybrid retrieval logic against the abstract storage contract.

Alternative considered: let the workbench pipeline call LanceDB directly. Rejected because it would bypass the repository abstraction introduced by the foundation work and make testing harder.

### Decision: Add explicit LanceDB readiness validation to shared startup guidance

The shared runtime readiness checks will validate both the processed file layout and the LanceDB artifact. If LanceDB is missing, malformed, or incompatible with the current embedding configuration, startup surfaces will fail with a remediation path that points back to `ingest` as the supported recovery command.

This keeps operator guidance coherent and avoids silent fallback to the old in-memory path, which would mask whether the proposal is actually in effect.

Alternative considered: silently fall back to in-memory vector indexing. Rejected because it would reintroduce the original ambiguity and make debugging persistence issues harder.

## Risks / Trade-offs

- [Risk] Indexing during `ingest` adds provider dependencies and runtime cost to the ingestion path. -> Mitigation: keep one shared embedding backend, batch embedding calls, and surface LanceDB indexing progress and counts in the ingestion summary.
- [Risk] A changed embedding model or backend could make an existing LanceDB index incompatible with query embeddings. -> Mitigation: persist index metadata for the embedding backend and fail readiness checks when the configured backend no longer matches the stored index.
- [Risk] Retrieval correctness could regress if LanceDB filtering metadata does not mirror the existing in-memory chunk metadata closely enough. -> Mitigation: store the identifiers and filter fields already used by the hybrid retrieval flow, then add retrieval parity tests against representative scoped questions.
- [Risk] Remote answer mode semantics change because it no longer owns corpus embeddings. -> Mitigation: document the split clearly and include retrieval debug fields that show the shared embedding backend and the separate answer provider.

## Migration Plan

1. Add LanceDB and a file-backed retrieval store implementation that can upsert chunk rows and execute filtered similarity search through the existing `RetrievalStore` interface.
2. Extend `ingest` to build the shared LanceDB artifact after normalized chunk generation, record index metadata, and report index output in the CLI summary.
3. Update runtime bootstrap and readiness checks to require the LanceDB artifact and to open a shared retrieval store for app, CLI, and evaluation entrypoints.
4. Refactor the workbench pipeline so provider mode selects answer narration only, while query embeddings come from the shared indexing backend.
5. Add regression coverage for ingestion-produced LanceDB artifacts, readiness failures, provider-mode diagnostics, and scoped retrieval behavior against the persisted store.

## Open Questions

- None. This proposal intentionally fixes the storage boundary, index-build timing, and shared-embedding strategy so implementation can proceed without further architectural choices.
