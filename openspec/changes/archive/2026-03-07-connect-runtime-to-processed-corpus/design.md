## Context

The archived ingestion work now defines and produces normalized artifacts outside `data/raw/`, but the runtime surfaces still answer against a seeded demo corpus. That means the project demonstrates pipeline structure without actually consuming the processed corpus that ingestion is meant to generate.

This change bridges that gap by replacing seeded runtime data with a bootstrap layer that reads `data/processed` and populates the repositories already used by planning, retrieval, calculation, and answer assembly. It must preserve the existing workbench pipeline shape so app, evaluation runner, and CLI stay aligned.

## Goals / Non-Goals

**Goals:**
- Load processed filings, chunks, and facts into the runtime repositories used by the query pipeline.
- Make app, evaluation, and CLI reuse the same processed-corpus bootstrap path.
- Keep provider selection orthogonal so `local` and future remote provider modes run over the same runtime data.
- Report clear startup failures when processed artifacts are absent or malformed.

**Non-Goals:**
- Re-running ingestion automatically from the app or CLI.
- Replacing the existing repository/service contracts.
- Introducing a new storage backend unless the processed artifact format requires it.
- Expanding benchmark scoring or provider contracts beyond what is necessary to run on processed data.

## Decisions

### Decision: Add a dedicated processed-runtime bootstrap module
The implementation will introduce a runtime loader that reads normalized processed artifacts and populates repository implementations before query execution. This keeps data loading isolated from the Streamlit app and CLI entrypoints.

Alternative considered: let each surface parse processed files on its own. Rejected because it would duplicate bootstrap logic and make behavior drift across app, evaluation, and CLI.

### Decision: Preserve `WorkbenchPipeline` as the shared application boundary
The runtime should continue to expose the existing workbench/query pipeline entrypoint, but swap the underlying corpus source from seeded fixtures to processed artifacts. This limits surface changes and keeps future provider work compatible.

Alternative considered: create a second pipeline type for processed data. Rejected because the runtime distinction is data source, not a different application contract.

### Decision: Fail fast when processed artifacts are unavailable
If the required processed files are missing, the runtime will raise a clear startup error that tells the operator to run the ingestion pipeline first. It will not silently fall back to the seeded corpus once this change is enabled.

Alternative considered: automatically fall back to demo fixtures. Rejected because it hides data readiness problems and undermines confidence in benchmark results.

### Decision: Load processed artifacts into the existing repository abstractions first
The first implementation should reuse the current typed repository and retrieval interfaces by loading processed artifacts into the repository layer, even if the source on disk is file-based. This minimizes behavioral drift while leaving room for later storage optimization.

Alternative considered: bind retrieval directly to file reads. Rejected because it bypasses the repository/service contracts the rest of the system already depends on.

## Risks / Trade-offs

- [Risk] Processed artifact shape may not perfectly match runtime expectations. -> Mitigation: add loader validation and fixture-backed tests for malformed or missing files.
- [Risk] Loading the full processed corpus at startup may be slower than the seeded demo corpus. -> Mitigation: keep the bootstrap explicit and cache the loaded runtime per process.
- [Risk] Switching the runtime corpus can expose benchmark failures that were previously hidden by fixtures. -> Mitigation: treat those as expected signals and keep evaluation outputs reproducible.
- [Risk] Provider work can diverge if it is implemented against the seeded corpus only. -> Mitigation: keep provider mode selection above the shared runtime bootstrap boundary.

## Migration Plan

1. Define the processed artifact set the runtime expects from `data/processed`.
2. Implement the bootstrap module that loads those artifacts into repositories.
3. Replace seeded-corpus startup in app, evaluation runner, and CLI with the shared processed runtime.
4. Add explicit error handling for missing or invalid processed data.
5. Validate the end-to-end runtime against existing processed artifacts and update smoke tests.

## Open Questions

- None. This change assumes processed data must already exist before runtime execution starts.
