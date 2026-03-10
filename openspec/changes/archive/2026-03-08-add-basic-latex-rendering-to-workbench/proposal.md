## Why

The Streamlit workbench currently renders the answer body as plain markdown, so
financial formulas are difficult to read and can be misinterpreted when users
ask for calculation-heavy explanations. A basic LaTeX-aware rendering path is
needed now to improve demo clarity without changing the grounded QA pipeline.

## What Changes

- Add math-aware answer rendering in the Streamlit workbench so block LaTeX
  expressions are displayed as formulas instead of raw text.
- Keep regular narrative and citation text rendering unchanged for non-math
  content.
- Add safe fallback behavior when malformed LaTeX is present so the UI remains
  usable and answer visibility is preserved.
- Add regression tests that cover formula rendering, non-formula currency text,
  and malformed formula fallback.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `demo-evaluation-workbench`: answer presentation requirements will be extended
  so the UI can render basic LaTeX content while preserving existing debug and
  citation display behavior.

## Impact

- Affected code: `app.py` answer rendering path and related UI helper logic.
- Affected tests: evaluation/workbench UI rendering tests for answer display.
- No new external service dependency is required for the MVP implementation.
