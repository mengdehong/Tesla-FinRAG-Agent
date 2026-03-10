## 1. Structured Planning

- [x] 1.1 Add a provider hook for structured JSON requests
- [x] 1.2 Implement an LLM-first planner that merges structured results into `QueryPlan`

## 2. Fallback Safety

- [x] 2.1 Reuse the existing rule planner as the fallback path
- [x] 2.2 Record planner mode and confidence diagnostics in the plan

## 3. Validation

- [x] 3.1 Add planner unit tests for fallback and typed diagnostics
- [x] 3.2 Run existing planner-intent regression tests
