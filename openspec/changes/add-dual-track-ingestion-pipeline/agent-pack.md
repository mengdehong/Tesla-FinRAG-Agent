# Agent Implementation Pack

## Shared Rules

- Change: `add-dual-track-ingestion-pipeline`
- Integrator owns the normalized output contract, metadata schema, and processed path conventions.
- Workers must not modify foundation-level shared types without integrator approval.
- `data/raw/` is read-only input; all derived outputs go elsewhere.

## Integrator Responsibilities

- Confirm the canonical `period_key` format and processed output layout.
- Merge parser and fact normalizer outputs into the shared corpus contract.
- Run final ingestion regression checks and update task status after implementation.

## ING-AGENT-1

- Change: `add-dual-track-ingestion-pipeline`
- Goal: Build the filing manifest, source inventory, and gap reporting flow for the target Tesla corpus.
- Depends on: `bootstrap-project-foundation`
- Write scope: `src/ingestion/sources`, `src/ingestion/manifest`, manifest-focused tests, processed manifest outputs.
- Do not touch: retrieval logic, UI code, answer generation modules.
- Inputs: current `data/raw/` inventory, SEC source strategy in the design, and shared foundation models.
- Deliverables: target corpus enumeration, local source reconciliation, and explicit missing-filing reporting including the 2025 FY 10-K gap.
- Validation commands: `uv run pytest -q tests`, manifest-specific smoke command if implemented.
- Done when: operators can inspect a manifest and know exactly which filings are present, missing, or pending download.

## ING-AGENT-2

- Change: `add-dual-track-ingestion-pipeline`
- Goal: Implement narrative section parsing and table chunk extraction with provenance metadata.
- Depends on: `ING-AGENT-1`
- Write scope: `src/ingestion/parsers`, `src/ingestion/chunking`, parser-focused tests, sample normalized chunk fixtures.
- Do not touch: fact normalization logic, retrieval ranking code, UI code.
- Inputs: manifest identities, raw filing sources, and shared chunk models from the foundation change.
- Deliverables: normalized narrative chunks and table chunks with section paths, source identifiers, and time metadata.
- Validation commands: `uv run pytest -q tests`
- Done when: a representative filing produces distinct narrative and table chunk outputs with traceable provenance.

## ING-AGENT-3

- Change: `add-dual-track-ingestion-pipeline`
- Goal: Normalize XBRL/companyfacts records into typed fact outputs aligned by metric, unit, and `period_key`.
- Depends on: `ING-AGENT-1`
- Write scope: `src/ingestion/facts`, fact normalization tests, fact fixtures, processed fact outputs.
- Do not touch: narrative parser modules, retrieval code, UI code.
- Inputs: existing `data/raw/companyfacts.json`, manifest metadata, and shared fact models.
- Deliverables: normalized fact records ready for calculator and retrieval consumers.
- Validation commands: `uv run pytest -q tests`
- Done when: representative metrics can be loaded as structured fact records without depending on raw PDF tables.

## ING-AGENT-4

- Change: `add-dual-track-ingestion-pipeline`
- Goal: Implement processed data writing and ingestion regression coverage across manifest, chunks, tables, and facts.
- Depends on: `ING-AGENT-2`, `ING-AGENT-3`
- Write scope: `src/ingestion/persistence`, ingestion regression tests, processed output helper modules.
- Do not touch: retrieval ranking, answer generation, Streamlit UI.
- Inputs: normalized chunk and fact outputs from the other ingestion workers.
- Deliverables: processed corpus persistence, ingestion regression suite, and end-to-end ingestion smoke validation.
- Validation commands: `uv run pytest -q`, `uv run ruff check .`
- Done when: the repository can produce a traceable normalized corpus and the known source gaps are surfaced explicitly.
