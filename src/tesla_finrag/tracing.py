"""Helpers for summarizing agent traces."""

from __future__ import annotations

from uuid import uuid4

from tesla_finrag.models import AgentIterationTrace


def new_trace_id() -> str:
    """Return a stable-enough trace identifier for one agent run."""
    return str(uuid4())


def summarize_agent_trace(traces: list[AgentIterationTrace]) -> dict[str, object]:
    """Build a compact summary for retrieval debug and evaluation artifacts."""
    actions = [
        trace.selected_action.action_type.value
        for trace in traces
        if trace.selected_action is not None
    ]
    return {
        "iterations": len(traces),
        "actions": actions,
        "had_no_progress_iteration": any(trace.no_progress for trace in traces),
    }
