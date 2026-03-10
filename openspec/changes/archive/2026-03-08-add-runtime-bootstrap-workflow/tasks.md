## 1. CLI Bootstrap Surface

- [x] 1.1 Extend `src/tesla_finrag/__main__.py` with a supported `ingest` subcommand that wraps the existing ingestion pipeline using repository default paths.
- [x] 1.2 Add concise ingestion completion reporting that surfaces the processed output location, normalized artifact counts, and manifest gap summary.
- [x] 1.3 Introduce a shared processed-corpus guidance helper so missing or malformed runtime artifacts map to one consistent remediation message and next-step command.

## 2. Runtime Surface Integration

- [x] 2.1 Update the `ask` CLI flow to use the shared processed-corpus guidance when runtime bootstrap fails.
- [x] 2.2 Update evaluation startup paths to surface the same readiness guidance instead of raw processed-corpus exceptions.
- [x] 2.3 Update `app.py` startup handling so the Streamlit workbench shows the same actionable processed-data remediation command.

## 3. Documentation And Validation

- [x] 3.1 Update `README.md` with the supported happy-path bootstrap sequence from `uv sync` through ingestion and local runtime launch.
- [x] 3.2 Add or update a developer-facing doc under `docs/` that explains processed-corpus prerequisites, troubleshooting, and validation commands for the runtime bootstrap workflow.
- [x] 3.3 Add tests covering the new ingestion CLI, shared processed-corpus guidance, and workspace/runtime startup behavior when processed artifacts are missing or invalid.
- [x] 3.4 Run the relevant validation commands for the change and confirm the documented bootstrap path matches actual repository behavior.
