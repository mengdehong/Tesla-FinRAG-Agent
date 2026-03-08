## 1. LanceDB Storage Foundation

- [x] 1.1 Add the `lancedb` dependency and define the shared indexing settings and metadata contract for the processed LanceDB artifact.
- [x] 1.2 Implement a LanceDB-backed `RetrievalStore` that can upsert section and table chunk rows, persist index metadata, and execute filtered similarity search by `doc_id` and chunk kind.

## 2. Ingestion Index Build

- [x] 2.1 Extend the ingestion pipeline so a successful `ingest` run builds or refreshes `data/processed/lancedb` from normalized section and table chunks.
- [x] 2.2 Update ingestion reporting to show LanceDB output status, indexed chunk counts, and any indexing failures as part of the CLI summary.

## 3. Runtime Retrieval Integration

- [x] 3.1 Update shared runtime bootstrap and readiness checks to validate the LanceDB artifact and open a persistent retrieval store for app, CLI, and evaluation entrypoints.
- [x] 3.2 Refactor the workbench pipeline so provider mode uses the shared indexed embedding backend for query embeddings and no longer rebuilds an in-memory corpus vector index at startup.
- [x] 3.3 Extend retrieval diagnostics and operator guidance to report the LanceDB path, indexed embedding backend, and actionable remediation when the index is missing or incompatible.

## 4. Validation And Documentation

- [x] 4.1 Add regression tests for LanceDB index creation during ingestion, stale-index refresh behavior, and runtime startup failures when the LanceDB artifact is missing or invalid.
- [x] 4.2 Add retrieval-path tests that confirm scoped hybrid search works against the LanceDB store and that provider diagnostics reflect the shared embedding backend plus selected answer provider.
- [x] 4.3 Update bootstrap and runtime documentation so operators know that `ingest` writes `data/processed/lancedb` and that runtime queries depend on that persisted index.
