# Agent Implementation Pack

## Shared Rules

- Change: `bootstrap-project-foundation`
- Integrator owns OpenSpec artifacts and any edits to shared contract files after worker feedback.
- Workers must not edit `data/raw/`, other OpenSpec changes, or files outside their declared write scope.
- If a worker needs a contract change outside its scope, it must hand the request back to the integrator.

## Integrator Responsibilities

- Freeze the package name, shared model module locations, and repository/service interface module locations.
- Merge worker outputs in dependency order.
- Run final validation for the change and update task status when implementation is complete.

## FND-AGENT-1

- Change: `bootstrap-project-foundation`
- Goal: Create the `uv` project scaffold, dependency manifest, and base package/test layout.
- Depends on: None.
- Write scope: `pyproject.toml`, `uv.lock`, package root under `src/`, top-level tooling config files, baseline `tests/` package markers.
- Do not touch: `openspec/changes/`, `data/raw/`, future feature modules.
- Inputs: `docs/PROJECT.md`, `docs/DECISION.md`, `docs/research/01-ProjectInitResearch.md`
- Deliverables: reproducible Python project structure and baseline dependency/tooling configuration.
- Validation commands: `uv sync`, `uv run python -V`
- Done when: a fresh clone can install dependencies and import the base package without manual path hacks.

## FND-AGENT-2

- Change: `bootstrap-project-foundation`
- Goal: Implement canonical typed models, settings, and logging helpers for shared use by later changes.
- Depends on: `FND-AGENT-1`
- Write scope: shared domain model modules under `src/`, settings/config modules, logging utility modules, model-focused tests.
- Do not touch: retrieval/inference logic, UI code, OpenSpec artifacts.
- Inputs: the frozen package layout from `FND-AGENT-1` and the interface expectations in this change's design.
- Deliverables: typed models for filings, chunks, facts, query plans, evidence bundles, answer payloads, and settings helpers.
- Validation commands: `uv run pytest -q tests`, `uv run ruff check .`
- Done when: shared schemas validate expected payloads and later modules can import them without redefining types.

## FND-AGENT-3

- Change: `bootstrap-project-foundation`
- Goal: Add repository/service interfaces, test doubles, and baseline smoke tests for the core pipeline boundaries.
- Depends on: `FND-AGENT-2`
- Write scope: interface modules under `src/`, fixture or fake implementations, boundary tests under `tests/`.
- Do not touch: final backend implementations, ingestion parsing code, UI code.
- Inputs: shared types from `FND-AGENT-2` and the design decisions in this change.
- Deliverables: replaceable repository/service contracts and smoke tests for configuration, models, and interface-level behavior.
- Validation commands: `uv run pytest -q`, `uv run ruff check .`
- Done when: the project has stable boundary contracts and all baseline validation commands pass.
