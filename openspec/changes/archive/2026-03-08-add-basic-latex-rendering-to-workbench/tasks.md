## 1. Rendering helper and integration

- [x] 1.1 Add a UI helper that splits answer text into ordered text segments and block-math segments for `$$...$$` and `\[...\]` delimiters.
- [x] 1.2 Integrate the helper into the workbench answer section so block-math segments use `st.latex` and text segments keep markdown rendering.
- [x] 1.3 Implement fail-open fallback so malformed block-math segments are rendered as plain text without interrupting the rest of the answer view.

## 2. Regression coverage

- [x] 2.1 Add tests for block formula detection and segment ordering in mixed answer content.
- [x] 2.2 Add tests ensuring currency expressions (for example `$96.77B`) remain in the plain-text lane when no block delimiters are present.
- [x] 2.3 Add tests that verify malformed formula blocks do not break answer, citation, or retrieval debug visibility.

## 3. Validation

- [x] 3.1 Run `uv run pytest -q` and fix any failures related to answer rendering changes.
- [x] 3.2 Run `uv run ruff check .` and resolve any lint issues introduced by the change.
