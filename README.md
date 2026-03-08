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

# 2. Start Ollama for local mode (default)
ollama serve
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text

# 3. Run ingestion to build the processed corpus and LanceDB vector index
uv run python -m tesla_finrag ingest

# 4. Launch the Streamlit workbench
uv run streamlit run app.py

# Or ask a question directly from the CLI
uv run python -m tesla_finrag ask --question "What was Tesla's total revenue in FY2023?"
```

If `data/processed/` is missing or incomplete, the runtime surfaces will
tell you exactly which command to run to fix it.

The `ingest` command now also builds a persistent **LanceDB** vector index
under `data/processed/lancedb/`.  Runtime queries and the Streamlit workbench
use this persisted index for semantic retrieval instead of rebuilding an
in-memory vector store on every startup.

## Provider Modes

- `local` is the default mode and uses Ollama for grounded answer narration.
- Retrieval embeddings come from the shared indexing backend configured by
  `INDEXING_EMBEDDING_*`; by default that is also Ollama at `http://localhost:11434/v1`.
- `openai-compatible` is the explicit remote mode and continues to use the
  configured `OPENAI_*` settings for answer narration only.
- If your remote environment uses a SOCKS proxy, the project now installs
  `httpx[socks]` as part of the runtime dependency set.

## Ingestion Notes

- `ingest` now reuses unchanged filing artifacts and unchanged `companyfacts` output across reruns.
- The completion summary distinguishes `Reprocessed`, `Reused`, and `Failed filings`, plus whether facts were reused.
- A run is only considered successful if the LanceDB index was built successfully; indexing failures now fail `ingest`.
- Automatic parallelism now sizes itself to the filings that still need parsing rather than the total manifest size.
- On this repo's raw corpus, ingestion is expected to be minute-scale rather than instant because PDF parsing is CPU-heavy.
- If you want the most conservative debugging path, force sequential mode:

```bash
uv run python -m tesla_finrag ingest --workers 1
```

- If you need a guaranteed full rebuild, clear `data/processed/` and rerun ingestion:

```bash
rm -rf data/processed
uv run python -m tesla_finrag ingest
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
  - `data/processed/lancedb/`: persistent vector index for semantic retrieval
- `docs/`: developer-facing documentation
- `openspec/`: change proposals, specs, and task tracking
