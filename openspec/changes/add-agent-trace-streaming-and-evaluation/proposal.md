## Why

Once the runtime becomes multi-step, we need better observability. Without structured trace events, evaluation and future UI progress rendering cannot distinguish a smart repair loop from a slow black box.

## What Changes

- Add streaming-friendly agent event types and payloads.
- Expose additive agent traces, halt reasons, and attempted actions in answer diagnostics.
- Add tests that validate the streaming path and the new observability contract.
- Prepare the backend for future “thinking progress” UI work without changing the current Streamlit rendering flow.

## Capabilities

### New Capabilities
- `agent-trace-streaming`: Stream agent progress as typed events for backend consumers and future UI surfaces.

### Modified Capabilities
- `demo-evaluation-workbench`: Backend execution now exposes additive agent trace events and observability metadata.
- `grounded-financial-qa`: Final answers now carry structured agent trace diagnostics.

## Impact

- Adds `AgentEvent`/related enums to shared models.
- Adds a `run_stream(...)` path to the workbench pipeline.
- Extends provider and evaluation-facing tests to cover the new agent path.
