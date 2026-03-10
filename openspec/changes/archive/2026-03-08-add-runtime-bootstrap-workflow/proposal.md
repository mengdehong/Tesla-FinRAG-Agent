## Why

The repository already has raw Tesla filings, a normalized ingestion pipeline, and runtime surfaces, but it still lacks a formal operator workflow that takes a developer from a fresh clone to a runnable demo with clear commands and readiness checks. Right now the project can fail on missing `data/processed` artifacts without giving a complete bootstrap path, which makes the intended ingestion-to-runtime chain harder to validate and demo.

## What Changes

- Add a standard runtime bootstrap workflow that defines the recommended path from raw filings to a runnable local demo.
- Add a formal ingestion CLI entrypoint that writes the normalized corpus to `data/processed` and reports what it produced.
- Make runtime startup checks actionable by telling operators exactly how to generate or bootstrap processed artifacts when they are missing.
- Document the startup chain in `README` and developer-facing docs so a fresh clone can be brought up without guesswork.
- Optionally provide one of two lightweight bootstrap aids for local development: a minimal fixture processed corpus or a dedicated bootstrap/dev-setup command that prepares a runnable local state.

## Capabilities

### New Capabilities
- `runtime-bootstrap-workflow`: Defines the supported end-to-end bootstrap path from repository checkout to a runnable local runtime, including preflight checks, remediation guidance, and an optional lightweight development bootstrap path.

### Modified Capabilities
- `filing-ingestion`: Add an operator-facing CLI workflow for producing the processed corpus and reporting normalized output status.
- `processed-corpus-runtime`: Tighten startup behavior so missing processed artifacts produce actionable remediation guidance instead of a generic prerequisite failure.
- `developer-workspace`: Extend developer bootstrap expectations to include the documented runtime startup chain and local bootstrap guidance.
- `demo-evaluation-workbench`: Clarify workbench startup behavior when processed data is absent so operators get the same guided bootstrap path as the shared runtime.

## Impact

- Affected code: package CLI, ingestion pipeline entrypoints, runtime bootstrap error handling, workbench startup flow, and developer documentation.
- Affected APIs: local operator commands for ingestion and runtime startup, plus startup error messages exposed by CLI and Streamlit surfaces.
- Affected systems: `data/raw/` to `data/processed/` ingestion flow, local demo startup, evaluation startup, and repository onboarding.
- Dependencies: no mandatory new external service dependency; optional bootstrap support may introduce a small fixture artifact or a local convenience command.
