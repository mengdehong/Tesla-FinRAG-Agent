# Agent Implementation Pack

## Shared Rules

- Change: `add-hybrid-retrieval-and-answer-pipeline`
- Integrator owns `QueryPlan`, `EvidenceBundle`, `AnswerPayload`, and any ranking or scoring contract exposed across modules.
- Workers must not rewrite ingestion output schemas; they consume the normalized corpus contract from the prior change.
- Retrieval, calculation, and answer composition stay in separate modules even if implementations are lightweight.

## Integrator Responsibilities

- Freeze the query planning output schema and answer payload fields before parallel work starts.
- Merge retrieval, calculation, and answer composition in dependency order.
- Run the representative end-to-end question suite before marking tasks complete.

## RAG-AGENT-1

- Change: `add-hybrid-retrieval-and-answer-pipeline`
- Goal: Implement repository and index access for metadata-aware hybrid retrieval over the normalized corpus.
- Depends on: `add-dual-track-ingestion-pipeline`
- Write scope: `src/retrieval`, retrieval repository tests, index helper modules, backend adapter code.
- Do not touch: Streamlit UI, ingestion parsers, final answer rendering prompts.
- Inputs: normalized corpus contract, project decision to prefer LanceDB, and shared retrieval interfaces.
- Deliverables: retrieval repository methods, metadata filter support, and hybrid search plumbing.
- Validation commands: `uv run pytest -q tests`, backend smoke command if added.
- Done when: the system can retrieve candidate evidence using exact terms, vectors, and hard period filters.

## RAG-AGENT-2

- Change: `add-hybrid-retrieval-and-answer-pipeline`
- Goal: Implement structured query planning for periods, metrics, question type, and filter extraction.
- Depends on: `add-dual-track-ingestion-pipeline`
- Write scope: `src/answering/query_planning`, planner-focused tests, parsing fixtures.
- Do not touch: UI code, ingestion source adapters, backend persistence modules outside planner dependencies.
- Inputs: shared `QueryPlan` model and representative complex questions from the project docs.
- Deliverables: deterministic planner outputs for period-scoped, comparison, and calculation-heavy questions.
- Validation commands: `uv run pytest -q tests`
- Done when: downstream services can consume `QueryPlan` without reparsing the raw question.

## RAG-AGENT-3

- Change: `add-hybrid-retrieval-and-answer-pipeline`
- Goal: Implement evidence linking and structured financial calculations over aligned facts and narrative evidence.
- Depends on: `RAG-AGENT-1`, `RAG-AGENT-2`
- Write scope: `src/answering/calculation`, `src/answering/evidence`, calculator-focused tests, regression fixtures.
- Do not touch: Streamlit UI, raw ingestion parser modules, OpenSpec artifacts.
- Inputs: retrieved evidence candidates, shared fact models, and planner outputs.
- Deliverables: explicit calculation and evidence-linking services for aggregation, comparison, and ranking workflows.
- Validation commands: `uv run pytest -q tests`
- Done when: the project can compute representative Tesla financial comparisons without delegating math to a language model.

## RAG-AGENT-4

- Change: `add-hybrid-retrieval-and-answer-pipeline`
- Goal: Assemble the final grounded answer payload and end-to-end integration coverage.
- Depends on: `RAG-AGENT-1`, `RAG-AGENT-2`, `RAG-AGENT-3`
- Write scope: `src/answering/service`, answer payload helpers, integration tests under `tests/answering` or equivalent.
- Do not touch: ingestion parsing code, Streamlit UI screens.
- Inputs: planner output, retrieved evidence, calculator results, and shared `AnswerPayload`.
- Deliverables: answer composition layer that returns answer text, citations, calculation steps, retrieval debug details, and limitation signals.
- Validation commands: `uv run pytest -q`, `uv run ruff check .`
- Done when: representative text, numeric, and text-plus-table questions pass end-to-end integration tests.
