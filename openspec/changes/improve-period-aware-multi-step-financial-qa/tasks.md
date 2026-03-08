## 1. Period-aware planning and retrieval

- [x] 1.1 Extend planning models and planner logic to represent period semantics, exact concept resolution, and decomposed sub-queries for multi-period questions.
- [x] 1.2 Update hybrid retrieval and evidence linking to execute period-aware retrieval units and merge them without losing required temporal coverage.

## 2. Calculation and grounded answer behavior

- [x] 2.1 Add derived-period logic and period-compatibility validation to structured calculations and fact selection.
- [x] 2.2 Enforce evidence sufficiency guardrails in answer composition and propagate limitation reasons through the answer payload debug metadata.

## 3. Regression coverage

- [x] 3.1 Add tests for cross-year comparisons, multi-quarter ranking, Q4 derivation, exact metric disambiguation, and incompatible-period rejection.
- [x] 3.2 Refresh benchmark expectations or supporting fixtures that change under the new period-aware grounding behavior.
