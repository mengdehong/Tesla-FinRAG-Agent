## Context

The repository already implements typed query planning, hybrid retrieval, structured calculation, and grounded answer composition. The remaining delivery gap is concentrated in questions that span multiple periods or require derived period semantics, exact metric resolution, and strict evidence thresholds. These questions currently fail when the pipeline retrieves one period but not another, mixes annual cumulative values with standalone quarters, or allows answer generation to proceed despite missing grounded evidence.

## Goals / Non-Goals

**Goals:**
- Represent period semantics explicitly enough to distinguish annual, quarterly, cumulative, and derived values.
- Decompose multi-period and comparison questions into retrievable sub-units before final answer assembly.
- Tighten concept resolution so standard metrics prefer the correct authoritative fact family.
- Return limitation states when required evidence is missing or incompatible.

**Non-Goals:**
- Replace the rule-based planner with a fully LLM-native agent framework.
- Add a new storage backend or new external reasoning service.
- Solve every narrative synthesis issue unrelated to period-aware financial grounding.

## Decisions

### 1. Query planning will emit period-aware sub-queries
The planner should keep a top-level query plan while also emitting normalized sub-queries for each required period or comparison leg. Retrieval will execute these sub-queries separately, then merge evidence into a single bundle. This preserves exact temporal coverage instead of hoping one fused retrieval pass finds every needed period.

Alternative considered: increasing top-k and relying on a single retrieval pass. Rejected because it does not guarantee balanced evidence across periods and tends to favor the most semantically obvious period.

### 2. Period semantics will be first-class in calculation and validation
Facts and derived results should be classified by period semantics such as annual cumulative, quarterly standalone, and derived standalone quarter. Calculation logic will validate compatible semantics before computing ratios or comparisons and will explicitly derive Q4-like values from compatible annual and quarterly inputs when needed.

Alternative considered: leaving semantics implicit in period_end dates. Rejected because period_end alone cannot distinguish FY cumulative values from standalone quarter values.

### 3. Concept resolution will prefer exact canonical concepts over broad alias matches
Metric extraction and fact selection should prefer exact canonical concepts, namespace-aware precedence, and period-compatible facts before falling back to broader aliases. This reduces errors where generic terms such as revenue or margin accidentally bind to a narrower custom concept.

Alternative considered: keeping substring-first alias resolution. Rejected because it is the direct cause of several documented benchmark failures.

### 4. Grounding guardrails will block unsupported answer narration
If the pipeline cannot assemble the required grounded facts or compatible evidence for a requested metric and period, answer generation should stop at a limitation status rather than narrating a plausible answer from partial context. This keeps the system aligned with the project's explicit grounding goals.

Alternative considered: allowing provider-backed narration to proceed with a warning. Rejected because it weakens evaluation trust and obscures retrieval defects.

## Risks / Trade-offs

- [Risk] Period-aware decomposition increases retrieval and merge complexity. → Mitigation: keep decomposition limited to explicit comparison and multi-period cases, and preserve existing single-pass behavior for simple questions.
- [Risk] Derived-period logic can introduce incorrect arithmetic when source semantics are ambiguous. → Mitigation: derive only from validated compatible inputs and return limitation status when semantics remain uncertain.
- [Risk] Stricter evidence thresholds may reduce answer coverage in the short term. → Mitigation: expose insufficiency reasons in retrieval debug so retrieval gaps can be improved deliberately.

## Migration Plan

- Extend typed planning and answer-debug models to represent sub-queries and period semantics.
- Update retrieval, calculation, and answer composition together so stricter validation does not break existing flows silently.
- Refresh regression fixtures and benchmark expectations after the new grounding behavior lands.

## Open Questions

- Whether the final public debug payload should expose raw sub-query text or only summarized period semantics can be finalized during implementation.
