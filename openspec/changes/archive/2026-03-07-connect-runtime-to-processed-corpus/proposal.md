## Why

The demo, evaluation runner, and CLI still execute over a seeded in-memory corpus rather than the normalized artifacts produced by ingestion. That keeps the demo reproducible, but it prevents the runtime from exercising the actual processed data path that the project is supposed to demonstrate.

## What Changes

- Add a runtime bootstrap layer that loads filings, chunks, and fact records from `data/processed` into the repositories used by the workbench pipeline.
- Make the Streamlit workbench, evaluation runner, and package CLI share the same processed-corpus runtime instead of the seeded demo corpus.
- Fail explicitly when required processed artifacts are missing instead of silently reverting to the seeded corpus.
- Preserve the provider abstraction so local and remote provider modes both operate on the same processed runtime.

## Capabilities

### New Capabilities
- `processed-corpus-runtime`: Runtime bootstrap for loading normalized processed artifacts into the query pipeline used by app, evaluation, and CLI flows.

### Modified Capabilities
- `demo-evaluation-workbench`: The demo and evaluation surfaces run against the processed corpus runtime rather than a seeded fixture corpus.

## Impact

- Affected code: runtime bootstrap, repository loading, workbench pipeline wiring, evaluation runner, and package CLI.
- Affected APIs: runtime startup behavior, error reporting when processed artifacts are missing, and shared pipeline bootstrapping.
- Dependencies: no required new dependency; reuses the existing typed repository and service layers.
- Systems: `data/processed` artifact consumption, local demo execution, benchmark execution, and future provider-enabled runtime paths.
