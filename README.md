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
# 1. Install dependencies
uv sync

# 2. Run ingestion to build the processed corpus
uv run python -m tesla_finrag ingest

# 3. Launch the Streamlit workbench
uv run streamlit run app.py

# Or ask a question directly from the CLI
uv run python -m tesla_finrag ask --question "What was Tesla's total revenue in FY2023?"
```

If `data/processed/` is missing or incomplete, the runtime surfaces will
tell you exactly which command to run to fix it.

## Ingestion Notes

- `ingest` now shows per-filing progress and runs with automatic parallelism by default.
- On this repo's raw corpus, ingestion is expected to be minute-scale rather than instant because PDF parsing is CPU-heavy.
- If you want the most conservative debugging path, force sequential mode:

```bash
uv run python -m tesla_finrag ingest --workers 1
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
- `data/processed/`: normalized artifacts produced by `ingest` (not committed)
- `docs/`: developer-facing documentation
- `openspec/`: change proposals, specs, and task tracking
