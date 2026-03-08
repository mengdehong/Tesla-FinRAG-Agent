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
- Worker count
- Output location
- Counts of filings, section chunks, table chunks, and fact records
- Failed filing count (if any)
- Any manifest gaps (expected filings not found in the raw data)

During the run, the CLI prints per-filing progress so a long-running ingest
does not look stalled. On the bundled raw corpus, expect minute-scale runtime;
PDF parsing is CPU-heavy.

#### Custom paths

```bash
uv run python -m tesla_finrag ingest --raw-dir /path/to/raw --output-dir /path/to/output
```

#### Sequential debugging mode

If you want the most predictable path while debugging a problematic filing:

```bash
uv run python -m tesla_finrag ingest --workers 1
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
└── facts/
    └── all_facts.jsonl   # One FactRecord JSON object per line
```

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
