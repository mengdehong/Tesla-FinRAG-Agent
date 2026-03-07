## 1. Retrieval infrastructure

- [ ] 1.1 Implement the retrieval repository and index access layer over the normalized corpus with metadata-aware query support.
- [ ] 1.2 Implement hybrid search orchestration that combines lexical matching, vector similarity, and result fusion.

## 2. Query planning and evidence linking

- [ ] 2.1 Implement query planning that extracts periods, metrics, scope filters, and answer intent into `QueryPlan`.
- [ ] 2.2 Implement evidence linking that aligns narrative chunks, table chunks, and fact records around shared periods and metrics.

## 3. Calculation and answer composition

- [ ] 3.1 Implement the structured financial calculator for aggregation, period-over-period change, and ranking use cases.
- [ ] 3.2 Implement grounded answer composition that returns citations, calculation steps, retrieval debug data, and confidence cues.

## 4. Integration validation

- [ ] 4.1 Add integration tests for text-only, numeric, and text-plus-table financial questions.
- [ ] 4.2 Verify representative complex Tesla questions return traceable answers without delegating arithmetic to free-form generation.
