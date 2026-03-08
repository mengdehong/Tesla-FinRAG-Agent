## Context

The repository already contains benchmark questions, failure analyses, and a repeatable runner, but those assets do not yet define a stable current baseline for the real processed corpus, and there is no delivery-facing report that summarizes current readiness. This makes it difficult to tell whether the latest repository state is improving, regressing, or merely accumulating artifacts.

## Goals / Non-Goals

**Goals:**
- Define a repeatable workflow that produces a current baseline run artifact tied to the processed corpus and current pipeline behavior.
- Make the latest accepted baseline easy to discover without inspecting every run file manually.
- Require failure analyses to reference current benchmark outputs rather than stale examples.
- Produce a delivery-facing report that summarizes architecture, evaluation status, known gaps, and demo instructions.

**Non-Goals:**
- Build a full reporting web application separate from the existing Streamlit workbench.
- Turn every piece of project status into an automatically generated document.
- Change the core QA pipeline behavior beyond the evaluation and reporting surfaces needed to assess it.

## Decisions

### 1. The evaluation workflow will maintain a stable "latest baseline" artifact
In addition to timestamped run files, the evaluation workflow should update a stable summary artifact that points to the latest accepted baseline run and its top-line metrics. This gives operators one canonical place to inspect current status.

Alternative considered: using only timestamped run files. Rejected because it makes status discovery manual and error-prone.

### 2. Failure analyses will be anchored to concrete run outputs
Failure analysis entries should reference the originating run or question result they describe. This keeps failure triage aligned with the current system rather than becoming a static brainstorming artifact detached from actual outputs.

Alternative considered: leaving failure analyses as free-standing examples. Rejected because it weakens their value as regression evidence.

### 3. The delivery report will be a checked-in Markdown artifact with evidence-backed claims
The final delivery summary should live in version-controlled documentation, with stable sections for architecture, corpus coverage, benchmark status, known limitations, and demo instructions. Quantitative claims should cite benchmark or corpus artifacts rather than rely on prose-only assertions.

Alternative considered: generating the entire report from JSON automatically. Rejected because the final deliverable still requires curated explanation and interpretation.

## Risks / Trade-offs

- [Risk] A stable baseline summary can become stale if contributors update run files without refreshing the pointer. → Mitigation: make the baseline update part of the documented evaluation workflow and add integrity tests around the pointer artifact.
- [Risk] Delivery documentation can drift from the real system state. → Mitigation: require benchmark and corpus references inside the report and refresh it whenever baseline metrics or known risks change.
- [Risk] Failure analyses tied too tightly to one run can become noisy. → Mitigation: store stable question and run references while allowing the narrative root cause section to synthesize repeated failure patterns.

## Migration Plan

- Update evaluation artifacts and runner outputs to produce a latest-baseline summary.
- Refresh failure analyses against a newly generated current baseline.
- Add the delivery report and link it from the documented operator workflow.

## Open Questions

- Whether the latest baseline pointer should be JSON-only or accompanied by a Markdown summary can be decided during implementation if both remain stable and easy to test.
