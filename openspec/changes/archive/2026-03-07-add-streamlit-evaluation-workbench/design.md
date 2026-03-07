## Context

After the retrieval and answer pipeline exists, the project still needs a visible delivery surface and a repeatable way to judge answer quality. The interview brief explicitly asks for a Streamlit interface, complex cross-document questions, and at least five failure analyses. This change therefore packages the backend pipeline into a local demo while also building the evaluation loop that makes future iterations measurable.

## Goals / Non-Goals

**Goals:**

- Build a Streamlit interface that accepts questions, applies scope filters, and displays answer payload details.
- Surface citations, calculation steps, and retrieval debug information in a way that supports demo walkthroughs and diagnosis.
- Curate a complex evaluation set that reflects the interview problem shape.
- Capture and persist structured failure analyses with symptom, root cause, and mitigation fields.
- Add a repeatable regression harness that reruns the benchmark and summarizes outcomes.

**Non-Goals:**

- Build a production-ready multi-user web application.
- Solve all UI polish or observability concerns beyond the local demo needs.
- Replace the underlying retrieval and answer pipeline with a new architecture.

## Decisions

### Decision: the UI is a workbench, not a chat shell

The Streamlit app will focus on inspectability: scope filters, answer blocks, citations, calculation steps, and retrieval debug details. This is preferred over a minimalist chat-only UI because the project must demonstrate why an answer is grounded.

Alternative considered: a bare text box with answer output only. Rejected because it hides the evidence and makes failure analysis much harder.

### Decision: evaluation artifacts are first-class project outputs

Complex questions, failure cases, and regression summaries will be stored as repository artifacts rather than informal notes. This keeps quality tracking versioned and repeatable.

Alternative considered: run ad hoc manual tests during demo preparation. Rejected because the interview brief explicitly asks for deeper failure analysis.

### Decision: failure analysis uses a structured template

Every recorded failure will capture the symptom, retrieval or reasoning breakdown, root cause hypothesis, and a concrete improvement direction. This makes the evaluation output actionable for future changes instead of being a loose narrative.

Alternative considered: store only wrong answers or screenshots. Rejected because it does not explain what to fix next.

## Risks / Trade-offs

- [Risk] The UI may tempt implementers to add new backend logic directly in Streamlit. -> Mitigation: keep the UI dependent on the existing answer service contract.
- [Risk] Evaluation coverage may overfit to a small set of questions. -> Mitigation: ensure the question set spans cross-year comparison, calculation, and text-plus-table linkage.
- [Risk] Failure analysis can become subjective. -> Mitigation: require structured fields and tie each case to evidence from a concrete run.

## Migration Plan

1. Build the Streamlit workbench around the existing answer payload.
2. Curate the evaluation dataset and expected metadata for each benchmark question.
3. Implement failure-analysis artifact generation and storage.
4. Implement a regression runner that executes the benchmark and summarizes outcomes.
5. Package the resulting demo and evaluation workflow for local use.

## Open Questions

- The first revision can keep evaluation storage file-based; only if the artifact volume grows should a richer persistence layer be considered.
- If answer latency becomes an issue in the demo, any caching should wrap the answer service rather than altering the UI contract.
