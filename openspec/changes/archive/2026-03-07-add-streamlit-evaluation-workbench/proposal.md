## Why

The project is meant to be demonstrated and evaluated, not just implemented as hidden backend services. To satisfy the interview deliverable, the system needs a usable Streamlit interface, a complex question benchmark, explicit failure analysis, and a repeatable regression workflow that shows where the system works and where it still fails.

## What Changes

- Add a Streamlit workbench for query input, scope controls, answer display, citations, and retrieval debug information.
- Add a curated complex evaluation set that exercises cross-document, cross-period, and calculation-heavy Tesla questions.
- Add a structured failure analysis workflow for low-quality or incorrect answers.
- Add a repeatable regression harness so demo and evaluation runs can be rerun after later fixes.

## Capabilities

### New Capabilities
- `demo-evaluation-workbench`: A local demo and evaluation layer that exposes grounded answers, evidence traces, and repeatable quality checks.

### Modified Capabilities
- None.

## Impact

- Affected code: Streamlit UI modules, application wiring, evaluation datasets, regression scripts, and reporting outputs.
- Affected APIs: query request handling, answer payload display shape, and evaluation result schemas.
- Dependencies: Streamlit and any lightweight reporting helpers needed for evaluation output.
- Systems: local demo workflow, benchmark execution, and failure-analysis reporting.
