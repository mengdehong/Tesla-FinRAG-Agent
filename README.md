# Tesla FinRAG

Financial RAG workspace for Tesla SEC filings (`10-K` and `10-Q`).

The repository is currently bootstrapped with the shared models, settings,
service boundaries, and validation commands needed for downstream ingestion,
retrieval, and UI work.

## Requirements

- Python 3.12
- `uv`

## Quick Start

```bash
uv sync
uv run python -m tesla_finrag --version
```

## Validation

```bash
uv run pytest -q
uv run ruff check .
```

## Layout

- `src/tesla_finrag/`: application package and shared contracts
- `tests/`: baseline model, settings, and interface tests
- `data/raw/`: immutable Tesla filing source inputs
- `openspec/`: change proposals, specs, and task tracking
