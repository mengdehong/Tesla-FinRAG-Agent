## Why

The current execution path is still a mostly one-way pipeline. When evidence is missing, the system can stop too early instead of trying safe repairs such as alternate concepts or broader table collection. We need a bounded agent loop with memory so the system can repair intelligently without drifting into unbounded retries.

## What Changes

- Add a bounded `FinancialQaAgent` around planning, retrieval, evidence assessment, repair, and answer generation.
- Add state memory that records attempted action signatures, no-progress streaks, and halt reasons.
- Add deterministic repair actions for concept repair and broader table retrieval, with optional LLM table extraction.
- Preserve the existing `(plan, bundle, answer)` contract for callers.

## Capabilities

### New Capabilities
- `financial-qa-agent-loop`: Run bounded agent-style repairs around grounded financial QA.

### Modified Capabilities
- `grounded-financial-qa`: Missing evidence is now handled by bounded repair attempts before the system stops.
- `demo-evaluation-workbench`: The backend pipeline now executes through the bounded agent loop while keeping its external return contract.

## Impact

- Adds a new `tesla_finrag.agent` module.
- Extends settings with iteration and table-extraction controls.
- Enriches answer diagnostics with agent trace and halt metadata.
