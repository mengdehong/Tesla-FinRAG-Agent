## Why

Normalized source data alone is not enough to answer the interview-style Tesla questions, which combine time scoping, financial terminology, cross-document evidence, and explicit calculations. The project needs a retrieval and answer pipeline that plans the query, finds the right evidence, performs structured calculations, and returns grounded answers with traceable citations.

## What Changes

- Add query planning that detects time periods, metrics, filters, and answer strategy.
- Add hybrid retrieval that combines keyword matching, vector similarity, and metadata filtering over the normalized corpus.
- Add evidence linking and financial calculation services for explicit numeric reasoning.
- Add grounded answer payload generation with citations, calculation steps, and retrieval debug data.

## Capabilities

### New Capabilities
- `grounded-financial-qa`: A retrieval and answer pipeline that produces citation-backed financial answers from normalized Tesla filing data.

### Modified Capabilities
- None.

## Impact

- Affected code: retrieval repositories, query planning, calculation services, answer composition, and integration tests.
- Affected APIs: query request shape, retrieval debug payload, and answer payload contracts consumed by UI and evaluation layers.
- Dependencies: embedding provider integrations, LanceDB or equivalent retrieval backend implementation, and ranking utilities.
- Systems: normalized corpus consumers, hybrid search, answer generation, and later Streamlit demo behavior.
