## 1. Retrieval infrastructure

- [x] 1.1 Implement the retrieval repository and index access layer over the normalized corpus with metadata-aware query support.
- [x] 1.2 Implement hybrid search orchestration that combines lexical matching, vector similarity, and result fusion.

## 2. Query planning and evidence linking

- [x] 2.1 Implement query planning that extracts periods, metrics, scope filters, and answer intent into `QueryPlan`.
- [x] 2.2 Implement evidence linking that aligns narrative chunks, table chunks, and fact records around shared periods and metrics.

## 3. Calculation and answer composition

- [x] 3.1 Implement the structured financial calculator for aggregation, period-over-period change, and ranking use cases.
- [x] 3.2 Implement grounded answer composition that returns citations, calculation steps, retrieval debug data, and confidence cues.

## 4. Integration validation

- [x] 4.1 Add integration tests for text-only, numeric, and text-plus-table financial questions.
- [x] 4.2 Verify representative complex Tesla questions return traceable answers without delegating arithmetic to free-form generation.
