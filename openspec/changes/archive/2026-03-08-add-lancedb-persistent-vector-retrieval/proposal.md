## Why

The project currently declares LanceDB as the preferred local retrieval backend, but the runtime never actually opens a LanceDB database or writes vector data into one. Each query surface still loads JSON artifacts into in-memory repositories and rebuilds the vector index from scratch at runtime, which makes retrieval persistence, startup behavior, and operator expectations inconsistent with the documented architecture.

## What Changes

- Make LanceDB a real runtime dependency and the default persistent vector retrieval backend for processed filing chunks.
- Extend `ingest` so it builds and updates a shared LanceDB index under `data/processed/lancedb` from the normalized section and table chunks it already emits.
- Change the runtime bootstrap and question-answering pipeline to connect to the persisted LanceDB index instead of rebuilding an in-memory vector store on startup.
- Standardize on one shared embedding backend for LanceDB indexing and query embeddings so the same persistent index works across both local and `openai-compatible` answer modes.
- Add operator-visible readiness and failure guidance so missing, stale, or malformed LanceDB state is reported alongside existing processed-corpus checks.
- Preserve `data/processed/` JSON and JSONL artifacts as the source of truth for normalized filings and facts in this change; facts do not move into LanceDB.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `filing-ingestion`: ingestion must persist and refresh the LanceDB vector index together with processed chunk artifacts.
- `grounded-financial-qa`: hybrid retrieval must use a persistent LanceDB vector lane rather than a per-process in-memory vector store.
- `openai-compatible-demo-pipeline`: remote answer mode must use the shared indexed embedding backend instead of rebuilding a separate provider-specific corpus vector space.
- `processed-corpus-runtime`: runtime bootstrap must validate and open the processed LanceDB index as part of the shared query surfaces startup path.
- `runtime-bootstrap-workflow`: the supported local bootstrap path must include generating and verifying the LanceDB index as part of corpus preparation.

## Impact

- Affected code: ingestion pipeline, runtime bootstrap, retrieval store implementation, settings, provider-backed workbench pipeline, diagnostics, and regression tests.
- Affected APIs: operator-facing `ingest` and readiness workflows, retrieval debug metadata, and startup error guidance for app, CLI, and evaluation surfaces.
- Dependencies: add the `lancedb` runtime dependency and any small supporting utilities needed for local index management.
- Affected systems: `data/processed/` generation, local retrieval persistence, provider-mode retrieval behavior, Streamlit/CLI startup behavior, and developer expectations around whether data is written into a database.
