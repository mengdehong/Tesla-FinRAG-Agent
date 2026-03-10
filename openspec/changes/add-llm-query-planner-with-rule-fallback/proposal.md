## Why

The current query planner is still fundamentally regex-driven. The repo already has provider abstractions and typed `QueryPlan` models, so the next step is to let an LLM do structured intent parsing while keeping a deterministic rule fallback for safety and regression stability.

## What Changes

- Add an LLM-first structured query planner that returns typed planning data.
- Add provider support for structured JSON planning requests.
- Preserve the existing rule planner as the compatibility and fallback path.
- Surface planner confidence, mode, and fallback diagnostics in the final plan.

## Capabilities

### New Capabilities
- `llm-query-planning`: Use structured LLM output to produce a typed `QueryPlan` with confidence and fallback metadata.

### Modified Capabilities
- `grounded-financial-qa`: Query planning now supports LLM-first structured parsing with deterministic fallback instead of a rule-only path.

## Impact

- Adds `tesla_finrag.planning.llm_query_planner`.
- Extends provider abstractions with structured JSON support.
- Extends `QueryPlan` with planner diagnostics and raw metric mention fields.
