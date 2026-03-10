## Why

The current ingestion path reparses the same filing PDF separately for narrative and table extraction, which makes full-corpus ingestion minute-scale even on a local developer machine. Re-running ingestion during development is also inefficient because unchanged filings and `companyfacts.json` are normalized again from scratch, which slows iteration on the real processed-corpus workflow.

## What Changes

- Optimize filing ingestion so each filing analysis run reuses a shared PDF pass for narrative and table extraction instead of reparsing the same document twice.
- Add incremental ingestion behavior that reuses existing processed artifacts for unchanged filings and unchanged `companyfacts` inputs.
- Extend ingestion reporting so operators can see how many filings were reprocessed, reused, or failed during a run.
- Preserve the logical processed-corpus contract consumed by the runtime and retrieval stack so downstream question answering behavior does not need to change.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `filing-ingestion`: Add repeated-run reuse behavior and richer ingestion run reporting while preserving the existing normalized corpus semantics.

## Impact

- Affected code: ingestion pipeline orchestration, PDF parsing helpers, processed artifact writing, and ingestion tests.
- Affected APIs: operator-facing `python -m tesla_finrag ingest` summary output and internal ingestion bookkeeping for reuse/skips.
- Affected systems: `data/raw/` to `data/processed/` normalization flow and developer iteration speed on the processed corpus.
- Dependencies: no required external service changes; the implementation may add a local ingestion state artifact under `data/processed`.
