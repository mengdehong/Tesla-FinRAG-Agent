## Context

The repo's current demo and evaluation tools consume a single final answer. After adding a repair loop, the backend needs a structured progress surface so operators can understand latency, action choices, and halting behavior.

## Goals / Non-Goals

**Goals:**
- Add typed agent events for plan, retrieval, repair, completion, and halt.
- Expose a generator-friendly backend interface without breaking current callers.
- Make the new path testable through unit tests and provider regressions.

**Non-Goals:**
- Render the event stream in the Streamlit UI yet.
- Build a new tracing storage backend.

## Decisions

- Add `AgentEventType`, `AgentHaltReason`, and `AgentEvent` to the shared model layer so traces stay serializable.
- Expose `WorkbenchPipeline.run_stream(...)` as the additive streaming-friendly path and let `run(...)` keep the synchronous compatibility contract.
- Store final agent traces in `retrieval_debug` until a dedicated typed answer trace surface becomes necessary.

## Risks / Trade-offs

- [Risk] Streaming and synchronous paths may diverge. -> Mitigation: keep synchronous `run()` backed by the same agent logic used in the streaming path.
- [Risk] Trace payloads may grow too large. -> Mitigation: keep events concise and emit summary traces rather than raw corpus dumps.
