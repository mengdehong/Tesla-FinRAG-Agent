## Context

The current ingestion pipeline is structurally correct but inefficient for both full runs and repeated local reruns. A single filing worker currently opens the same PDF twice: once to build narrative chunks and once to extract tables. That duplicated `pdfplumber` and `pdfminer` work dominates ingestion wall time on Tesla 10-K filings. The pipeline also reparses every filing and rewrites the processed corpus from scratch on each run, even when the raw PDFs and `companyfacts.json` have not changed.

This change is performance-focused but still cross-cutting because it affects the ingestion worker flow, output invalidation rules, processed artifact persistence, operator-facing summary reporting, and regression coverage. The design must preserve the downstream logical corpus contract so retrieval, calculation, and answer generation continue to consume the same normalized content model.

## Goals / Non-Goals

**Goals:**
- Reduce cold full-ingestion time by removing redundant PDF parsing work inside a filing job.
- Reduce repeated ingestion time by reusing existing processed artifacts when raw filing inputs and parser-relevant settings have not changed.
- Keep the processed-corpus semantics stable for the runtime and evaluation paths.
- Surface reuse versus reprocess outcomes clearly in the ingestion summary so operators can tell what work actually happened.

**Non-Goals:**
- Redesign retrieval, calculation, runtime bootstrap, or answer-generation behavior.
- Replace the PDF parsing stack with a new library in this change.
- Introduce a new processed-corpus schema for downstream consumers.
- Add remote caching or external infrastructure beyond local processed artifacts.

## Decisions

### Decision: Refactor filing ingestion into a shared single-pass PDF analysis path

Each filing worker will open the PDF once, extract page text once, derive section context once, and reuse that shared page analysis for both narrative chunk creation and table extraction metadata. The worker will continue to emit the same logical `SectionChunk` and `TableChunk` models, but it will stop treating narrative and tables as fully separate PDF parsing passes.

This captures the largest measured bottleneck without changing downstream repository contracts.

Alternative considered: keep the current split functions and only raise worker concurrency. Rejected because profiling already shows duplicated PDF parsing inside a single filing dominates runtime, so more workers mainly amplify CPU and memory contention.

### Decision: Add a local ingestion state artifact keyed by source and parser fingerprint

The ingestion flow will persist a lightweight state file under `data/processed` that records, for each filing and for `companyfacts`, the source fingerprint and a parser fingerprint derived from parser-relevant configuration and implementation version. On rerun, unchanged entries will reuse their existing processed artifacts, while changed or invalidated entries will be reparsed and rewritten.

This gives the biggest developer-iteration improvement while remaining local-first and debuggable.

Alternative considered: cache intermediate page-analysis blobs. Rejected because it increases storage complexity and compatibility burden beyond what is needed for the first performance-focused iteration.

### Decision: Preserve the current processed artifact contract and replace changed filing outputs atomically

The runtime will continue to see the same logical processed corpus layout and models after ingestion completes. When a filing is reprocessed, the pipeline will clear and rewrite only that filing's derived artifacts before updating the ingestion state entry. When an unchanged filing is reused, its existing artifacts remain untouched. `companyfacts` normalization will be handled with the same rule: rewrite fact outputs only when the source fingerprint or parser fingerprint changes.

This keeps runtime behavior stable and avoids coupling the performance change to a processed-corpus migration.

Alternative considered: switch chunk and table storage to a new aggregated file format. Rejected for this change because it would require synchronized runtime-loader contract updates and would expand the proposal beyond the highest-value performance bottlenecks.

### Decision: Make auto-parallelism depend on active reprocessing work, not total manifest size

The worker resolver will continue to allow explicit operator overrides, but the default path will size concurrency against the number of filings that actually need parsing after reuse checks, with a conservative upper bound suitable for `pdfminer`-heavy CPU workloads. This avoids paying high process overhead when incremental reuse reduces the active work set to a handful of filings.

Alternative considered: keep the current static auto-cap and ignore reuse-aware scheduling. Rejected because an incremental pipeline with only one or two changed filings should not spin up the same worker pool used for a full cold run.

## Risks / Trade-offs

- [Risk] Reuse logic could serve stale artifacts after parser code changes. -> Mitigation: include a parser fingerprint in the ingestion state and invalidate reuse whenever parser-relevant code or settings change.
- [Risk] Partial rewrites could leave a filing's artifact directory inconsistent after a failure. -> Mitigation: replace per-filing outputs with clear-then-write sequencing and only persist the new state entry after the filing rewrite succeeds.
- [Risk] Sharing one PDF analysis path across narrative and tables could accidentally change chunk boundaries or section assignment. -> Mitigation: add parity-style regression tests against representative 10-Q and 10-K fixtures to preserve current normalized semantics.
- [Risk] Incremental behavior adds debugging complexity when operators expect a full rebuild. -> Mitigation: report processed, reused, and invalidated counts explicitly and keep a supported path to force a full reprocess by clearing processed artifacts.

## Migration Plan

1. Introduce ingestion-state bookkeeping and source fingerprint helpers.
2. Refactor filing workers around a shared page-analysis result that feeds both narrative and table normalization.
3. Update writing logic so changed filings are selectively rewritten while unchanged filings are reused.
4. Extend CLI summary reporting and logging with processed versus reused counts and invalidation reasons where relevant.
5. Add regression coverage for parity, reuse, invalidation, and rerun behavior; then update operator docs.

## Open Questions

- None. The change intentionally preserves the current processed-corpus contract and focuses on local performance and repeatability.
