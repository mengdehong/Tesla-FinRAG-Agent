## Context

The repo already has useful repair primitives, especially evidence linking and table fallback. The missing piece is an orchestrator that can decide when to retry, what to try next, and when to stop without repeating the same failed action forever.

## Goals / Non-Goals

**Goals:**
- Introduce a bounded agent loop with explicit halt reasons.
- Prevent repeated `Action A -> fail -> Action A` cycles by tracking action signatures.
- Keep the existing deterministic calculator and grounded answer composer intact.

**Non-Goals:**
- Introduce an unconstrained autonomous agent framework.
- Redesign the Streamlit UI in this change.

## Decisions

- Add `AgentStateMemory` with attempted signatures, no-progress streak, and per-iteration traces.
- Restrict repairs to narrow, auditable actions: concept repair, table retrieval repair, and optional LLM table extraction.
- Keep `run(question) -> (plan, bundle, answer)` for compatibility, and build streaming support as an additive path.
- Attach agent traces to `retrieval_debug` so current callers and evaluation tools can inspect the loop without changing payload shape.

## Risks / Trade-offs

- [Risk] More iterations can increase latency. -> Mitigation: keep a small maximum iteration count and explicit no-progress stop conditions.
- [Risk] Repair actions may over-broaden retrieval. -> Mitigation: retain period/concept grounding and preserve typed diagnostics.
- [Risk] Optional LLM extraction could overfit to noisy tables. -> Mitigation: keep it behind a setting and use it after deterministic repairs.
