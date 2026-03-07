# Agent Implementation Pack

## Shared Rules

- Change: `add-streamlit-evaluation-workbench`
- Integrator owns the UI-to-answer-service contract and the schema for evaluation and failure-analysis artifacts.
- Workers must consume the existing answer payload rather than adding backend logic directly into the UI layer.
- Evaluation and failure artifacts should remain versioned project files, not transient notes.

## Integrator Responsibilities

- Freeze the answer payload fields that the Streamlit UI will render.
- Merge UI, benchmark, and regression artifacts so they produce one coherent local workflow.
- Run final demo and benchmark validation before marking the change complete.

## UI-AGENT-1

- Change: `add-streamlit-evaluation-workbench`
- Goal: Build the Streamlit workbench shell and render the shared answer payload with scope controls.
- Depends on: `add-hybrid-retrieval-and-answer-pipeline`
- Write scope: `app.py` or `src/ui`, Streamlit-specific helpers, UI smoke tests.
- Do not touch: retrieval ranking logic, ingestion parsers, core calculation logic.
- Inputs: shared `AnswerPayload`, demo goals from the proposal, and existing answer service wiring.
- Deliverables: a local Streamlit app with scoped query input and grounded answer display.
- Validation commands: `uv run streamlit run app.py`, UI smoke tests if present.
- Done when: a local operator can run the app and inspect answer text and citations for a real question.

## EVAL-AGENT-1

- Change: `add-streamlit-evaluation-workbench`
- Goal: Create the benchmark question set and expected metadata for complex Tesla financial evaluation.
- Depends on: `add-hybrid-retrieval-and-answer-pipeline`
- Write scope: evaluation dataset files under project-owned evaluation paths, benchmark helper modules, dataset tests.
- Do not touch: Streamlit rendering logic, retrieval repository code.
- Inputs: interview requirements, normalized corpus coverage, and representative question types from prior research.
- Deliverables: at least five complex benchmark questions with structured metadata about coverage and intent.
- Validation commands: evaluation dataset smoke command or `uv run pytest -q tests`
- Done when: the project contains a reusable benchmark set that exercises the intended problem space.

## EVAL-AGENT-2

- Change: `add-streamlit-evaluation-workbench`
- Goal: Implement structured failure-analysis artifacts and populate at least five concrete cases from current runs.
- Depends on: `EVAL-AGENT-1`
- Write scope: failure-analysis templates, analysis artifact files, reporting helpers, related tests.
- Do not touch: answer-generation core logic except to consume outputs for analysis.
- Inputs: benchmark runs, answer payload debug fields, and the structured analysis format from this change's design.
- Deliverables: versioned failure-analysis records with symptom, suspected root cause, and mitigation guidance.
- Validation commands: benchmark runner or `uv run pytest -q tests`
- Done when: the repository contains at least five actionable failure records tied to real evaluation runs.

## EVAL-AGENT-3

- Change: `add-streamlit-evaluation-workbench`
- Goal: Build the regression runner and final local demo workflow that ties the app and benchmark together.
- Depends on: `UI-AGENT-1`, `EVAL-AGENT-1`, `EVAL-AGENT-2`
- Write scope: regression runner scripts or modules, demo workflow docs, smoke tests for UI/evaluation wiring.
- Do not touch: ingestion normalization logic, hybrid retrieval internals.
- Inputs: Streamlit app entrypoint, benchmark dataset, and failure-analysis artifacts.
- Deliverables: repeatable evaluation execution and a documented local demo workflow.
- Validation commands: `uv run pytest -q`, `uv run streamlit run app.py`
- Done when: an operator can run both the demo and the benchmark workflow from the repository without improvisation.
