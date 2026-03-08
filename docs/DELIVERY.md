# Delivery Readiness Report

> **Baseline run:** `6307bfc151b1` (2026-03-07)
> **Pass rate:** 3/9 (33.33%)

## 1. Architecture Overview

Tesla FinRAG Agent is a multi-step financial question-answering system built on Retrieval-Augmented Generation (RAG). It ingests Tesla SEC filings (10-K annual and 10-Q quarterly reports from 2021–2025) and answers complex financial queries that require cross-document, cross-period, and numeric reasoning.

### Pipeline Stages

```
User Question
    ↓
┌─────────────────────────┐
│  Query Planner           │  Rule-based decomposition into query type,
│  (RuleBasedQueryPlanner) │  required periods, concepts, and sub-questions
└──────────┬──────────────┘
           ↓
┌─────────────────────────┐
│  Hybrid Retrieval        │  BM25 (lexical) + vector (semantic) + XBRL facts
│  (HybridRetrievalService)│  via LanceDB with metadata filtering
└──────────┬──────────────┘
           ↓
┌─────────────────────────┐
│  Structured Calculator   │  Numeric operations: ratios, differences,
│  (StructuredCalculator)  │  time-series trends, currency formatting
└──────────┬──────────────┘
           ↓
┌─────────────────────────┐
│  Evidence Linker         │  Links evidence chunks to citations
│  (EvidenceLinker)        │
└──────────┬──────────────┘
           ↓
┌─────────────────────────┐
│  Answer Composer         │  Generates grounded, cited answers
│  (GroundedAnswerComposer)│  with optional LLM narration
└──────────┴──────────────┘
```

### Key Design Decisions

1. **XBRL over PDF for numbers**: Authoritative financial figures come from SEC XBRL (`companyfacts.json`), eliminating PDF table extraction errors. PDF text is used for narrative sections (MD&A, risk factors).
2. **LanceDB for hybrid search**: Native BM25 + vector retrieval with SQL-level metadata filtering in a single embedded database. No external server required.
3. **Tools-based multi-step reasoning**: Custom query planner + structured calculator instead of long prompts or general-purpose agent frameworks. Deterministic arithmetic avoids LLM calculation errors.
4. **No framework dependency**: Custom function-calling pipeline rather than LangChain/LlamaIndex, for domain-specific control and transparency.

## 2. Corpus Coverage

### Source Data

| Filing Type | Years | Count |
|---|---|---|
| 10-K (Annual) | 2021–2025 | 5 |
| 10-Q (Quarterly) | 2021 Q1–Q3, 2022 Q1–Q3, 2023 Q1–Q3, 2024 Q1–Q3, 2025 Q1–Q3 | 15 |
| XBRL Facts | `companyfacts.json` | 1 |
| **Total source files** | | **21** |

Evidence source: `data/raw/` currently contains 5 annual PDFs matching `*_全年_10-K.pdf`, 15 quarterly PDFs matching `*_Q*_10-Q.pdf`, and `companyfacts.json`.

### Processed Artifacts

The ingestion pipeline (`uv run python -m tesla_finrag ingest`) produces:

- **Filing documents**: One JSON per filing with metadata (filing type, fiscal year/quarter, period end)
- **Section chunks**: Narrative text segments from MD&A, risk factors, and other filing sections
- **Table chunks**: Structured tables extracted from PDFs, stored as headers + rows
- **Fact records**: XBRL-sourced structured financial data (concept, value, unit, period)
- **LanceDB index**: Vector embeddings for hybrid (BM25 + semantic) retrieval

### XBRL Concept Coverage

The current fact store indexes the following concepts:
- `us-gaap:Revenues` — Total revenue
- `us-gaap:GrossProfit` — Gross profit
- `us-gaap:OperatingIncomeLoss` — Operating income
- `custom:FreeCashFlow` — Free cash flow
- `custom:CapitalExpenditure` — Capital expenditures

**Not currently indexed** (identified via failure analysis):
- `us-gaap:CostOfGoodsAndServicesSold`
- `us-gaap:ResearchAndDevelopmentExpense`
- `us-gaap:CashAndCashEquivalentsAtCarryingValue`

## 3. Benchmark Outcomes

### Latest Baseline (run `6307bfc151b1`, 2026-03-07)

| Metric | Value |
|---|---|
| Total questions | 9 |
| Pass | 3 |
| Fail | 6 |
| Error | 0 |
| Pass rate | **33.33%** |
| Avg latency | 0.34 ms |

### Per-Question Results

| ID | Category | Difficulty | Status | Notes |
|---|---|---|---|---|
| BQ-001 | cross_year | medium | PASS | Revenue YoY comparison: correctly computed 18.80% growth |
| BQ-002 | calculation | medium | FAIL | Gross margin ratio inverted (Revenue/GrossProfit instead of GrossProfit/Revenue) |
| BQ-003 | text_plus_table | hard | FAIL | INSUFFICIENT_EVIDENCE — CostOfGoodsAndServicesSold not in fact store |
| BQ-004 | time_sequenced | hard | FAIL | INSUFFICIENT_EVIDENCE — ResearchAndDevelopmentExpense not in fact store |
| BQ-005 | multi_period | hard | FAIL | Looked up non-existent concept `custom:OperatingMarginPercent` |
| BQ-006 | balance_sheet | medium | FAIL | INSUFFICIENT_EVIDENCE — CashAndCashEquivalentsAtCarryingValue not in fact store |
| BQ-007 | calculation | hard | FAIL | Correct FCF value but no step-by-step decomposition shown |
| BQ-008 | cross_year | easy | PASS | Simple FY2023 revenue lookup |
| BQ-009 | calculation | easy | PASS | Simple FY2023 free cash flow lookup |

### Passing Pattern

The system reliably handles:
- **Single-period fact lookups** (BQ-008, BQ-009)
- **Two-period comparison with percentage change** (BQ-001)

### Failure Patterns

See `data/evaluation/failure_analyses.json` (6 structured analyses, all anchored to baseline run `6307bfc151b1`).

| Pattern | Cases | Severity |
|---|---|---|
| XBRL concept not indexed | BQ-003, BQ-004, BQ-006 | major |
| Calculation direction error | BQ-002 | critical |
| Multi-quarter decomposition missing | BQ-005 | critical |
| Pre-computed aggregate without step trace | BQ-007 | major |

## 4. Known Limitations

1. **Limited XBRL concept coverage**: Only 5 concepts are indexed. Queries requiring R&D, COGS, or balance-sheet items return `INSUFFICIENT_EVIDENCE`.
2. **Ratio calculation direction**: The structured calculator can invert numerator/denominator for margin-type calculations (see FA-001).
3. **No multi-quarter decomposition**: Queries comparing multiple quarters are not decomposed into per-quarter sub-queries (see FA-004).
4. **No fallback from XBRL to table chunks**: When an XBRL fact is missing, the pipeline does not attempt to retrieve the value from PDF-extracted tables.
5. **Step-by-step trace suppressed for pre-computed facts**: When an aggregate fact exists (e.g., FreeCashFlow), the calculator returns it directly without showing component values.
6. **PDF table OCR quality**: Some PDF-extracted tables contain OCR artifacts (digit/letter confusion), though the current pipeline prefers XBRL facts for numeric answers.

## 5. Demo & Validation Guidance

### Prerequisites

```bash
# Install dependencies
uv sync

# Build processed corpus (required once; reruns skip unchanged filings)
uv run python -m tesla_finrag ingest
```

### Running the Demo

**Streamlit workbench** (interactive UI):
```bash
uv run streamlit run app.py
```

The workbench provides:
- Provider selection (local Ollama or remote OpenAI-compatible)
- Fiscal year and filing type filters
- Query input with answer display, citations, calculation steps, and retrieval debug

**CLI question answering**:
```bash
uv run python -m tesla_finrag ask -q "What was Tesla's total revenue in FY2023?"
```

### Running the Evaluation

```bash
# Run benchmark and save timestamped run artifact
uv run python -m tesla_finrag.evaluation.runner

# Accept the run as latest baseline (updates latest_baseline.json)
uv run python -m tesla_finrag.evaluation.runner --accept-baseline
```

This executes all 9 benchmark questions and saves a timestamped run file to `data/evaluation/runs/`. The stable pointer `data/evaluation/latest_baseline.json` is only updated when `--accept-baseline` is provided.

### Validation Commands

```bash
# Run tests
uv run pytest -q

# Lint check
uv run ruff check .

# Verify processed corpus readiness
uv run python -c "from tesla_finrag.guidance import check_corpus_readiness; r=check_corpus_readiness(); print(r or 'Corpus is ready')"
```

### Recommended Demo Questions

These questions currently pass the benchmark and produce good answers:

1. "What was Tesla's total revenue in FY2023?" (BQ-008)
2. "What was Tesla's free cash flow in FY2023?" (BQ-009)
3. "Compare Tesla's total revenue between FY2022 and FY2023. What was the year-over-year growth rate?" (BQ-001)

## 6. Artifact Locations

| Artifact | Path |
|---|---|
| Benchmark questions | `data/evaluation/benchmark_questions.json` |
| Failure analyses | `data/evaluation/failure_analyses.json` |
| Latest baseline pointer | `data/evaluation/latest_baseline.json` |
| Evaluation run history | `data/evaluation/runs/` |
| Processed corpus | `data/processed/` |
| This delivery report | `docs/DELIVERY.md` |
| Architecture decisions | `docs/DECISION.md` |
| Runtime bootstrap guide | `docs/runtime_bootstrap.md` |
