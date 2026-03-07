## 1. Workspace bootstrap

- [ ] 1.1 Initialize the Python project with `uv`, create the `src/` and `tests/` layout, and add baseline dependencies and developer tooling.
- [ ] 1.2 Add project-level configuration for Ruff, pytest, environment loading, and a minimal application package entry point.

## 2. Typed contracts and settings

- [ ] 2.1 Implement canonical typed models for filing documents, chunks, facts, query plans, evidence bundles, and answer payloads.
- [ ] 2.2 Implement structured settings and logging helpers that later changes can reuse without redefining environment handling.

## 3. Service boundaries and validation

- [ ] 3.1 Define repository and service interfaces for ingestion, retrieval, calculation, and answer generation with test doubles or fixtures.
- [ ] 3.2 Add baseline tests that exercise model validation, settings loading, and interface-level smoke behavior.
- [ ] 3.3 Verify `uv sync`, `uv run pytest -q`, and `uv run ruff check .` work as the standard completion gate for this change.
