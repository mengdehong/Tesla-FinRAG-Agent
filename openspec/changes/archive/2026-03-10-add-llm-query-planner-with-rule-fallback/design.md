## Context

The system already has a stable `QueryPlan` contract and robust rule-based fallbacks for periods and calculations. The design goal is to improve understanding without making runtime stability depend on the LLM always behaving correctly.

## Goals / Non-Goals

**Goals:**
- Prefer structured LLM planning when available.
- Fall back to `RuleBasedQueryPlanner` on low confidence or malformed JSON.
- Keep planner diagnostics typed and inspectable.

**Non-Goals:**
- Move arithmetic or final answer generation into the planner.
- Replace the current runtime orchestration in this change.

## Decisions

- The LLM planner requests JSON only and merges that output with existing deterministic helpers for period semantics, answer shape, and operand building.
- A planner confidence threshold gates acceptance; low-confidence plans fall back cleanly to rules.
- Concept resolution is delegated to the shared resolver instead of staying inside the planner's alias map.
- Structured JSON support lives in `provider.py` so both OpenAI-compatible and Ollama-backed modes can use the same planning hook.

## Risks / Trade-offs

- [Risk] Provider JSON responses may be malformed or unsupported. -> Mitigation: catch failures and return to the rule planner.
- [Risk] Planner confidence values may be noisy across providers. -> Mitigation: expose them diagnostically and keep rules as the safe default.
- [Risk] LLM parsing adds one extra remote call. -> Mitigation: keep the planner bounded and make later runtime changes responsible for latency trade-offs.
