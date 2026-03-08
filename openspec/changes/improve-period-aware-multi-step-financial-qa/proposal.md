## Why

The current query pipeline can answer simple numeric and narrative questions, but it still underperforms on the interview-style questions that combine multiple periods, derived quarters, exact metric resolution, and evidence sufficiency checks. The remaining delivery risk is no longer basic wiring; it is whether the system can answer cross-period financial questions without mixing scopes or inventing unsupported conclusions.

## What Changes

- Add period-aware query decomposition so comparison, ranking, and multi-period questions retrieve evidence per period before final answer assembly.
- Tighten concept resolution and period alignment rules so annual, quarterly, cumulative, and derived values are not mixed incorrectly.
- Add explicit support for derived-period calculations such as Q4-from-FY-minus-Q1-Q3 and related validation rules.
- Enforce evidence sufficiency guardrails so missing grounded evidence returns a limitation status instead of a speculative answer.
- Extend regression coverage for cross-year, multi-quarter, text-plus-table, and insufficient-evidence scenarios.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `grounded-financial-qa`: planning, retrieval, calculation, and answer-grounding requirements expand to support period-aware multi-step reasoning and stronger evidence guardrails.

## Impact

- Affected code: query planning, hybrid retrieval, evidence linking, structured calculations, answer composition, and regression tests.
- Affected APIs: `QueryPlan` and `AnswerPayload.retrieval_debug` may gain additional fields for sub-queries, period semantics, and evidence sufficiency decisions.
- Dependencies: no new external service is required; the change should build on the existing typed service-layer architecture.
- Systems: cross-period financial QA accuracy, failure-mode handling, and evaluation pass rate on complex benchmark questions.
