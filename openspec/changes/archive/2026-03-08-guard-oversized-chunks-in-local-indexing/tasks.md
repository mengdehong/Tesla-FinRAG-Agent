## 1. Index-safe ingestion

- [x] 1.1 Add an index-time segmentation helper for oversized narrative and table chunks, including conservative size budgets and fallback splitting.
- [x] 1.2 Update LanceDB index building to write one vector row per segment with source-chunk lineage metadata and explicit diagnostics for any still-unindexable chunk.

## 2. Runtime and retrieval compatibility

- [x] 2.1 Update the LanceDB retrieval store and hybrid retrieval flow to dedupe segmented hits back to the original processed chunk records used for evidence and citations.
- [x] 2.2 Replace runtime chunk-count equality checks with lineage-aware validation that accepts segmented indexes and rejects orphaned or inconsistent rows.

## 3. Validation and operator guidance

- [x] 3.1 Add regression tests for oversized table/narrative chunks, segmented index metadata, runtime bootstrap validation, and local Ollama indexing behavior.
- [x] 3.2 Update operator-facing guidance for rebuilding LanceDB after the schema change and run `openspec validate` plus targeted Python tests for the affected paths.
