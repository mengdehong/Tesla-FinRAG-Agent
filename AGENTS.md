# Repository Guidelines

## Project Structure & Module Organization
- `data/raw/`: source Tesla filings (`10-K`/`10-Q`) in PDF format and Json format.
- `docs/`: product intent and technical decisions (`PROJECT.md`, `DECISION.md`, `research/`).
- `openspec/`: spec-driven workflow configuration and change artifacts.
- `.codex/skills/`: local automation skills for propose/explore/apply/archive flows.
- Source code is expected to live under a future `src/` layout with matching tests under `tests/`.

## Build, Test, and Development Commands
- `uv sync`: install/update dependencies from lockfile.
- `uv add <package>`: add a runtime dependency.
- `uv add --dev <package>`: add a dev dependency (pytest, ruff, etc.).
- `uv run pytest -q`: run automated tests.
- `uv run ruff check .`: run lint checks.
- `uv run ruff format .`: apply formatting.
- `uv run streamlit run app.py`: launch local QA demo UI (when app entry exists).

## Coding Style & Naming Conventions
- Python 3.12+ with type hints (`pydantic` models for typed I/O boundaries).
- Follow Ruff defaults: 4-space indentation, max line length per Ruff config.
- Use `snake_case` for modules/functions/variables, `PascalCase` for classes, and descriptive file names (`retrieval_service.py`, `xbrl_repository.py`).
- Keep retrieval, calculation, and answer-generation logic in separate service modules.

## Testing Guidelines
- Framework: `pytest`.
- Test files: `tests/test_<module>.py`; test names: `test_<behavior>_<condition>()`.
- Add coverage for parsing, retrieval filtering, period handling, and numeric calculation correctness.
- For finance logic, include at least one fixture-backed regression test per bug fix.

## Commit & Pull Request Guidelines
- Use Conventional Commits as seen in history (`docs: ...`, `chore: ...`).
- Recommended format: `<type>(scope): <summary>`.
- PRs should include: purpose, key changes, validation commands run, and sample output/screenshots for UI changes.
- Link related OpenSpec change/task IDs when applicable.

## Security & Data Handling
- Do not commit API keys or tokens; use environment variables and a local `.env` (gitignored).
- Treat `data/raw/` as immutable source input; write derived artifacts to a separate processed path.
