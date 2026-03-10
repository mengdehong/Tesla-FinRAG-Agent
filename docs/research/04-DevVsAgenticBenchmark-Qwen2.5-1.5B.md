# Dev vs Agentic Benchmark on Qwen2.5:1.5B

## Scope

This note records the March 9, 2026 comparison between the `dev` worktree and
the `agentic-finrag-integration` worktree using the same local model stack and
the same processed Tesla corpus.

## Runtime Setup

- Chat model: `qwen2.5:1.5b`
- Embedding model: `nomic-embed-text`
- Chat base URL: `http://localhost:11434/v1`
- Embedding base URL: `http://localhost:11434/v1`
- Processed corpus: `/home/wenmou/Projects/Tesla-FinRAG-Agent/data/processed`
- Benchmark runner: `tesla_finrag.evaluation.EvaluationRunner().run_all()`

Environment variables used in both runs:

```bash
PROCESSED_DATA_DIR=/home/wenmou/Projects/Tesla-FinRAG-Agent/data/processed
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_CHAT_MODEL=qwen2.5:1.5b
INDEXING_EMBEDDING_BASE_URL=http://localhost:11434/v1
INDEXING_EMBEDDING_MODEL=nomic-embed-text
```

## Benchmark Results

| Branch | Passed | Failed | Avg Latency | Median | P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `dev` | 15/15 | 0 | 3945.88 ms | 3331.15 ms | 7835.24 ms |
| `agentic-finrag-integration` | 15/15 | 0 | 5987.08 ms | 5464.55 ms | 9386.66 ms |

Observed tradeoff before follow-up fixes:

- Accuracy on the existing benchmark was flat at `100%` for both branches.
- Agentic latency was materially higher:
  - `+2041.20 ms` average latency
  - `+2133.40 ms` median latency
  - `+1551.42 ms` p95 latency

## Niche Question Probe

We then asked four off-benchmark questions:

1. `What was Tesla's accounts payable current at the end of FY2024?`
2. `What was Tesla's public float according to the 2024 annual filing?`
3. `Compare Tesla's accounts receivable current between FY2023 and FY2024.`
4. `What geopolitical risks did Tesla mention in 2024, and what was accounts payable current at year-end?`

Both branches returned `status=ok` but mostly answered with an unrelated Free
Cash Flow template. This showed that the original 15-question benchmark was not
stress-testing obscure working-capital and DEI concepts.

## Why the Existing Benchmark Missed It

Two issues combined to hide the problem:

1. The niche questions were not part of `data/evaluation/benchmark_questions.json`.
2. The benchmark runner treated structured retrieval coverage as primary. When
   `expected_facts` or `expected_calc` passed, `expected_answer_contains` could
   still be ignored, allowing off-topic final answers to survive.

## Root Cause Analysis

### Why `required_concepts` Was Empty

- `WorkbenchPipeline` in the agentic worktree already used `LLMQueryPlanner`
  plus `SemanticConceptResolver`.
- Under `qwen2.5:1.5b`, the structured planner output was JSON-shaped but
  typed poorly:
  - `metric_mentions` came back as a dict
  - `planner_confidence` came back as `"High"`
- The planner previously only accepted:
  - list-shaped `metric_mentions`
  - numeric `planner_confidence`
- That collapsed confidence to `0.0`, triggered `llm_fallback`, and returned
  the old rule-based planner result.
- The rule fallback did not have aliases for `AccountsPayableCurrent`,
  `AccountsReceivableNetCurrent`, or `EntityPublicFloat`, so
  `required_concepts=[]`.

### Why the Concept Resolver Did Not Catch It

The resolver was not the primary failure point. Direct resolution tests showed:

- `accounts payable current -> us-gaap:AccountsPayableCurrent`
- `public float -> dei:EntityPublicFloat`

The problem was that the resolver only runs when `metric_mentions` is non-empty.
Once planner fallback produced an empty metric list, the resolver was bypassed.

### Why Free Cash Flow Could Still Be Judged as Success

- The pipeline returned `status=ok`, which already looks superficially healthy.
- The old benchmark decision rule let structured assertions override keyword
  mismatches.
- That meant a response could retrieve plausible evidence, ignore the actual
  question, and still be marked as passing.

## Changes Added After the Comparison

This worktree now includes follow-up hardening so the gap is visible and easier
to regress:

- Added niche benchmark cases `BQ-016` to `BQ-019`.
- Added matching failure analyses `FA-007` to `FA-010`.
- Tightened benchmark gating so explicit `expected_answer_contains` terms still
  matter even when structured assertions are present.
- Hardened planner coercion for dict-shaped `metric_mentions` and named
  confidence labels.
- Added curated fallback aliases for:
  - `us-gaap:AccountsPayableCurrent`
  - `us-gaap:AccountsReceivableNetCurrent`
  - `dei:EntityPublicFloat`
- Normalized mention noise such as `Tesla's public float` in the concept
  resolver.

## Important Note on Semantic Thresholds

The semantic resolver still exposes score and gap defaults, but those values are
model-calibrated placeholders rather than portable truths. If the embedding
backend changes, the acceptance policy must be recalibrated before using strict
semantic auto-accept in production.
