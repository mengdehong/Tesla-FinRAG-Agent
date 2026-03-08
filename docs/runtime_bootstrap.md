# Runtime Bootstrap Guide

This document describes the processed-corpus prerequisites, troubleshooting
steps, and validation commands for the Tesla FinRAG runtime bootstrap workflow.

## Prerequisites

| Requirement | Detail |
|---|---|
| Python | 3.12 |
| Package manager | `uv` |
| Raw filing data | Present under `data/raw/` (PDF filings + `companyfacts.json`) |

## Bootstrap Workflow

### 1. Install dependencies

```bash
uv sync
```

### 2. Build the processed corpus

The ingestion CLI reads filing PDFs and XBRL data from `data/raw/` and writes
normalized artifacts to `data/processed/`:

```bash
uv run python -m tesla_finrag ingest
```

On completion, the CLI prints a summary including:
- Worker count actually used
- Output location
- Counts of total filings, reprocessed filings, reused filings, section chunks, table chunks, and fact records
- Whether `companyfacts` output was reused
- LanceDB index status, path, and indexed chunk count
- Failed filing count (if any)
- Any manifest gaps (expected filings not found in the raw data)

During the run, the CLI prints per-filing progress so a long-running ingest
does not look stalled. On reruns, unchanged filings are skipped by consulting a
local ingestion state file under `data/processed/`, so small edits should only
reparse the invalidated filings. On the bundled raw corpus, expect minute-scale
runtime for cold runs; PDF parsing is CPU-heavy.

This release introduces a segmented LanceDB row schema (`index_schema_version: 2`)
for oversized chunk handling. If your existing `data/processed/lancedb/` was built
before this schema, rebuild the processed corpus so runtime bootstrap and retrieval
lineage checks can pass.

#### Custom paths

```bash
uv run python -m tesla_finrag ingest --raw-dir /path/to/raw --output-dir /path/to/output
```

#### Sequential debugging mode

If you want the most predictable path while debugging a problematic filing:

```bash
uv run python -m tesla_finrag ingest --workers 1
```

#### Force a full rebuild

If you need to invalidate every cached artifact and rebuild the processed
corpus from scratch, remove the processed output and rerun ingestion:

```bash
rm -rf data/processed
uv run python -m tesla_finrag ingest
```

### 3. Launch a runtime surface

**Streamlit workbench:**
```bash
uv run streamlit run app.py
```

**CLI question answering:**
```bash
uv run python -m tesla_finrag ask -q "What was Tesla's total revenue in FY2023?"
```

**Evaluation runner:**
```bash
uv run python -m tesla_finrag.evaluation.runner

# accept this run as latest baseline
uv run python -m tesla_finrag.evaluation.runner --accept-baseline
```

## Processed Corpus Layout

The runtime expects this layout under `data/processed/`:

```
data/processed/
├── filings/              # One JSON per FilingDocument
│   └── <doc_id>.json
├── chunks/               # Narrative section chunks
│   └── <doc_id>/
│       └── <chunk_id>.json
├── tables/               # Extracted table chunks
│   └── <doc_id>/
│       └── <chunk_id>.json
├── facts/
│   └── all_facts.jsonl   # One FactRecord JSON object per line
└── lancedb/              # Persistent vector index (built by ingest)
    ├── chunks.lance/     # LanceDB table data
    └── _index_metadata.json  # Embedding model, dimensions, build time
```

The LanceDB index uses the **shared indexing embedding backend** configured
via `INDEXING_EMBEDDING_MODEL` (default: `nomic-embed-text`) and
`INDEXING_EMBEDDING_BASE_URL` (default: Ollama at `http://localhost:11434/v1`).
If the backend requires authentication, also set `INDEXING_EMBEDDING_API_KEY`
or reuse `OPENAI_API_KEY` for the indexing client.
Both ingestion and runtime query embeddings share the same model to ensure
consistency.

`_index_metadata.json` now stores both `source_chunk_count` and
`vector_row_count`. `vector_row_count` can be higher than the processed chunk
count when large section/table chunks are segmented at indexing time.

## Troubleshooting

### Missing processed artifacts

**Symptom:** Runtime surfaces report an error like:

> Processed corpus not ready: Required processed artifact directory not found …

**Fix:** Run the ingestion pipeline:

```bash
uv run python -m tesla_finrag ingest
```

### Malformed processed artifacts

**Symptom:** Runtime surfaces report:

> Processed corpus invalid: Failed to parse …

**Fix:** Delete the corrupted output and regenerate:

```bash
rm -rf data/processed
uv run python -m tesla_finrag ingest
```

### Incompatible LanceDB index

**Symptom:** Runtime surfaces report:

> LanceDB index incompatible: LanceDB index was built with embedding model … but current configuration uses …

**Fix:** Re-run ingestion to rebuild the index with the current embedding model:

```bash
uv run python -m tesla_finrag ingest
```

If you are upgrading from a pre-segmentation index schema, force a clean rebuild:

```bash
rm -rf data/processed/lancedb
uv run python -m tesla_finrag ingest
```

The runtime now treats a missing metadata file, missing LanceDB table, or
chunk-count mismatch as an invalid processed corpus and will fail startup
instead of silently dropping the vector lane.

### Verifying the corpus

After ingestion, confirm the corpus can be fully loaded by the runtime:

```bash
uv run python -c "from tesla_finrag.guidance import check_corpus_readiness; r=check_corpus_readiness(); print(r or 'Corpus is ready')"
```

## Validation Commands

```bash
# Run the test suite
uv run pytest -q

# Lint check
uv run ruff check .

# Verify processed corpus readiness by loading the corpus
uv run python -c "from tesla_finrag.guidance import check_corpus_readiness; r=check_corpus_readiness(); print(r or 'Corpus is ready')"
```

## Evaluation & Delivery Artifacts

After running the evaluation workflow, the following artifacts are available:

| Artifact | Location | Description |
|---|---|---|
| Benchmark questions | `data/evaluation/benchmark_questions.json` | 9 complex financial questions covering 6 categories |
| Failure analyses | `data/evaluation/failure_analyses.json` | Structured diagnosis of failing questions, anchored to baseline run |
| Latest baseline | `data/evaluation/latest_baseline.json` | Stable pointer to the most recent accepted baseline run with top-line metrics |
| Run history | `data/evaluation/runs/` | Timestamped JSON files for every evaluation run |
| Delivery report | `docs/DELIVERY.md` | Architecture, benchmark outcomes, known limitations, and demo guidance |

### Typical Operator Workflow

```bash
# 1. Build the processed corpus
uv run python -m tesla_finrag ingest

# 2. Run the evaluation benchmark (saves timestamped run)
uv run python -m tesla_finrag.evaluation.runner

# 3. Accept the run as latest baseline (explicit operator action)
uv run python -m tesla_finrag.evaluation.runner --accept-baseline

# 4. Inspect the latest baseline
cat data/evaluation/latest_baseline.json

# 5. Review failure analyses
cat data/evaluation/failure_analyses.json

# 6. Read the delivery report
cat docs/DELIVERY.md

# 7. Launch the interactive demo
uv run streamlit run app.py
```
