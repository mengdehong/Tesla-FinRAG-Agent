## Context

The Streamlit workbench currently renders `answer.answer_text` through a single
`st.markdown(...)` call. This is sufficient for plain narrative answers, but
formula-like content is shown as raw text and is harder to inspect during
calculation-heavy demos. The change must stay UI-scoped: it should not alter
the retrieve-calculate-answer pipeline, answer payload schema, or citation
generation flow.

The project also uses currency-heavy financial prose (for example `$96.77B`),
so naive inline `$...$` parsing can misclassify normal finance text as math.

## Goals / Non-Goals

**Goals:**
- Provide a basic, stable LaTeX display experience in the Streamlit answer area.
- Preserve existing answer payload, citations, and retrieval debug behavior.
- Keep malformed LaTeX non-fatal by falling back to plain text rendering.
- Add focused regression tests for rendering edge cases.

**Non-Goals:**
- Rewriting answer generation prompts to force all numeric explanations into
  LaTeX.
- Introducing a full markdown parser dependency for the MVP.
- Supporting ambiguous inline `$...$` as first-phase behavior.
- Changing retrieval, planning, calculation, or data storage components.

## Decisions

### Decision: Block-math-only support for MVP
The renderer will recognize only explicit block delimiters (`$$...$$` and
`\[...\]`) and send those blocks to `st.latex`, while all other content stays
in markdown/text lanes.

Alternative considered: parse inline `$...$` immediately. Rejected for phase 1
because finance text frequently contains currency symbols and would increase
false positives and operator confusion.

### Decision: Keep rendering logic in a dedicated UI helper
Introduce a small helper that splits answer text into ordered render segments
(`text` vs `latex_block`) before calling Streamlit render APIs.

Alternative considered: embed regex branching directly in `app.py`. Rejected to
improve testability and keep the workbench view code readable.

### Decision: Fail-open rendering semantics
If a LaTeX block cannot be rendered, the UI will render the original block as
plain text and continue rendering the rest of the answer.

Alternative considered: surface a hard error and stop rendering. Rejected
because this degrades demo usability and hides grounded answer content.

## Risks / Trade-offs

- [Risk] Some valid inline formulas will remain unrendered in MVP.
  -> Mitigation: document as phase-2 enhancement after currency-safe rules are
  validated.
- [Risk] Regex-based block parsing may miss malformed delimiter patterns.
  -> Mitigation: add tests for malformed and mixed-content cases and preserve
  fail-open fallback.
- [Risk] Additional helper introduces small UI complexity.
  -> Mitigation: keep helper minimal and isolated to answer rendering.

## Migration Plan

1. Add/update `demo-evaluation-workbench` spec delta for math-aware rendering.
2. Implement the answer rendering helper and wire it into `app.py`.
3. Add regression tests for block formulas, currency text, and fallback path.
4. Run `uv run pytest -q` and `uv run ruff check .`.

## Open Questions

- Should phase 2 support inline `\(...\)` before `$...$` to reduce ambiguity?
