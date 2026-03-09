## MODIFIED Requirements

### Requirement: Query plan carries explicit calculation intent

The query planning service SHALL produce a `QueryPlan` that includes explicit calculation intent fields: `calculation_intent` (the type of calculation required), `calculation_operands` (typed operands with role annotations), `requires_step_trace` (whether decomposed calculation steps are needed), and `answer_shape` (the expected structure of the final answer). These fields SHALL be used by downstream calculator and composer services instead of inferring intent from concept list length or order.

#### Scenario: Gross margin question produces ratio intent with correct operands

- **WHEN** the user asks "What was Tesla's gross profit margin for FY2023?"
- **THEN** the query plan SHALL have `calculation_intent: "ratio"`, `calculation_operands` with `us-gaap:GrossProfit` as numerator and `us-gaap:Revenues` as denominator, and `required_concepts` containing both concepts

#### Scenario: Operating margin question does not use pseudo-concept

- **WHEN** the user asks "Which quarter had the highest operating margin?"
- **THEN** the query plan SHALL NOT contain `custom:OperatingMarginPercent` in `required_concepts`, and SHALL instead have `calculation_intent: "ratio"` with `us-gaap:OperatingIncomeLoss` as numerator and `us-gaap:Revenues` as denominator

#### Scenario: Free cash flow step trace question sets requires_step_trace

- **WHEN** the user asks "Calculate Tesla's free cash flow by subtracting capital expenditures from operating cash flow. Show each step."
- **THEN** the query plan SHALL have `requires_step_trace: true` and `calculation_intent: "step_trace"`

#### Scenario: Multi-period comparison sets ranking answer shape

- **WHEN** the user asks "Compare operating income across Q1, Q2, Q3 2023. Which quarter had the highest?"
- **THEN** the query plan SHALL have `answer_shape: "ranking"` and sub-queries for each quarter

#### Scenario: Single revenue lookup sets lookup intent

- **WHEN** the user asks "What was Tesla's total revenue in FY2023?"
- **THEN** the query plan SHALL have `calculation_intent: "lookup"` and `answer_shape: "single_value"`

#### Scenario: Year-over-year comparison sets pct_change intent

- **WHEN** the user asks "Compare Tesla's total revenue between FY2022 and FY2023. What was the year-over-year growth rate?"
- **THEN** the query plan SHALL have `calculation_intent: "pct_change"` and `answer_shape: "comparison"`

### Requirement: Answer composer uses calculation intent for routing

The answer composer SHALL use the `calculation_intent` and `calculation_operands` fields from the query plan to determine how to invoke the calculator, rather than inferring from `len(required_concepts)`. When `calculation_intent` is present, it SHALL take precedence over heuristic routing. When absent, the composer SHALL fall back to existing heuristic logic for backward compatibility.

#### Scenario: Ratio intent determines numerator and denominator

- **WHEN** the query plan has `calculation_intent: "ratio"` with operands specifying `us-gaap:GrossProfit` as numerator and `us-gaap:Revenues` as denominator
- **THEN** the composer SHALL invoke `compute_ratio` with gross profit as numerator and revenues as denominator, regardless of their order in `required_concepts`

#### Scenario: Fallback to heuristic when intent is absent

- **WHEN** the query plan has `calculation_intent: null` and `required_concepts: ["us-gaap:Revenues"]` with one required period
- **THEN** the composer SHALL use the existing single-concept single-period lookup logic
