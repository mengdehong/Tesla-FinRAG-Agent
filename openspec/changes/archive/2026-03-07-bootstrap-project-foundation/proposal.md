## Why

The repository currently contains Tesla raw filings and research notes, but it does not yet have a runnable Python project, stable typed boundaries, or repeatable validation commands. Without a shared foundation, later ingestion, retrieval, and UI work would either stall or conflict because multiple implementers would be forced to invent structure independently.

## What Changes

- Initialize the Python application workspace with `uv`, a `src/` layout, a `tests/` layout, and baseline quality commands.
- Establish canonical typed models and settings contracts that later changes must build against.
- Define stable repository and service boundaries for ingestion, retrieval, calculation, and answer generation.
- Add seed fixtures and validation gates so future changes can ship incrementally without reworking project setup.

## Capabilities

### New Capabilities
- `developer-workspace`: A reproducible project workspace with standard commands, typed configuration, and stable contracts for downstream implementation.

### Modified Capabilities
- None.

## Impact

- Affected code: future `src/`, `tests/`, and project-level config files such as `pyproject.toml`.
- Affected APIs: canonical domain types and service interfaces used by all later changes.
- Dependencies: `uv`, `pytest`, `ruff`, `pydantic`, and baseline application/runtime dependencies.
- Systems: local developer workflow, automated validation flow, and all downstream OpenSpec changes.
