## Context

By the time this change begins, the project is expected to have a normalized narrative/table corpus and period-aligned fact records. The next problem is orchestration: user questions about Tesla finances require more than semantic search. Queries often encode time constraints, financial metrics, and answer patterns such as comparison, ranking, or aggregation. The system therefore needs a structured query planner, hybrid evidence retrieval, explicit calculation, and grounded answer assembly.

## Goals / Non-Goals

**Goals:**

- Interpret user questions into structured query plans containing periods, metrics, filters, and answer intent.
- Retrieve evidence through a combination of lexical search, vector similarity, and hard metadata filtering.
- Link narrative, table, and fact evidence around shared time periods and metric identities.
- Perform explicit financial calculations outside free-form language generation.
- Return answer payloads that include citations, calculation steps, retrieval debug context, and confidence cues.

**Non-Goals:**

- Build the interactive UI itself.
- Optimize for production-scale latency beyond what is needed for a local-first demo.
- Introduce a heavyweight generic agent framework as the answer orchestration layer.

## Decisions

### Decision: use an explicit `QueryPlan` before retrieval

The answer pipeline will first convert a raw question into a `QueryPlan` that captures target periods, metrics, query type, retrieval filters, and whether calculation is required. This preserves the intended retrieve-calculate-answer architecture and gives later layers a typed contract instead of ambiguous prompt text.

Alternative considered: send the raw user question directly to a retriever and let the model infer everything downstream. Rejected because it weakens time handling and makes calculation behavior unreliable.

### Decision: combine BM25-style search, vectors, and metadata filtering

The retrieval layer will fuse lexical matching, semantic similarity, and hard filters on fields such as form type and `period_key`. This aligns with the project requirement to handle exact terms like "Free Cash Flow" and explicit periods such as "2022 Q3".

Alternative considered: pure vector search. Rejected because exact terms and explicit time scopes are too important in this domain.

### Decision: calculations run in a dedicated service

The pipeline will use a dedicated financial calculator or structured computation layer for aggregation, period-over-period change, and ranking. Language generation will explain the result, but it will not invent the math.

Alternative considered: let the answer model compute directly from retrieved snippets. Rejected because arithmetic reliability is a core project risk.

### Decision: answer payloads must carry full grounding context

Every final answer will include citations, calculation steps, retrieval debug fields, and a confidence or limitation signal. This is necessary for the later Streamlit demo and failure-analysis workflow.

Alternative considered: return only answer text plus a few raw references. Rejected because it would not support debugging or evaluation.

## Risks / Trade-offs

- [Risk] Hybrid retrieval tuning may require iteration to balance lexical and semantic evidence. -> Mitigation: expose retrieval debug details and keep ranking logic encapsulated.
- [Risk] Period resolution can be ambiguous for loosely phrased questions. -> Mitigation: store the planner output in the answer payload and prefer explicit constraints when present.
- [Risk] Calculation logic may need metric-specific treatment. -> Mitigation: keep calculator operations explicit and test them with regression fixtures.

## Migration Plan

1. Implement the retrieval repository and indexing interfaces over the normalized corpus.
2. Implement query planning for periods, metrics, and answer intent.
3. Implement evidence fusion and structured calculation services.
4. Implement grounded answer assembly with citations and debug payloads.
5. Add integration tests that cover representative complex financial questions.

## Open Questions

- The first implementation can use one default embedding provider behind a client interface, but the interface should remain provider-agnostic.
- If reranking is later added, it should be layered on top of the hybrid retrieval contract rather than changing the answer payload shape.
