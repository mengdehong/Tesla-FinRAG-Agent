## 1. Query Planning Contracts

- [x] 1.1 Add query language and normalized search fields to the query planning models used by `QueryPlan` and `SubQuery`
- [x] 1.2 Extend period extraction to recognize Chinese fiscal-year, quarter, and date expressions without regressing existing English behavior
- [x] 1.3 Extend metric, narrative cue, comparison, ranking, and calculation-intent extraction to support Chinese and mixed-language questions
- [x] 1.4 Generate normalized English retrieval text for whole-query and per-period sub-query planning outputs

## 2. Retrieval Adaptation

- [x] 2.1 Update hybrid retrieval to consume normalized query text and sub-query search text in both single-pass and per-period modes
- [x] 2.2 Replace the lexical tokenizer with a CJK-aware implementation that still preserves English, numeric, form-type, and period tokens
- [x] 2.3 Extend retrieval debug metadata to expose original query text, normalized query text, and per-sub-query search text for troubleshooting

## 3. Answer Experience

- [x] 3.1 Add language-adaptive answer and limitation templates to the grounded answer composer while preserving source-language citations
- [x] 3.2 Extend narrative cue prioritization and ratio-display logic so Chinese and mixed-language questions receive the same answer shaping as English questions

## 4. Validation

- [x] 4.1 Add planner unit tests for Chinese and mixed-language period, metric, and intent extraction
- [x] 4.2 Add lexical and hybrid retrieval tests covering Chinese query tokenization and normalized retrieval behavior
- [x] 4.3 Add composer and integration tests that verify Chinese answer wording, limitation wording, and benchmark-equivalent question coverage
- [x] 4.4 Run targeted pytest suites for planner, retrieval, composer, and integration coverage in the new worktree
