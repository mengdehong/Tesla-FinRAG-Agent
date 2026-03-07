## 1. Processed runtime bootstrap

- [x] 1.1 Define the processed artifact inputs required to start the runtime from `data/processed`.
- [x] 1.2 Implement a shared bootstrap module that loads processed filings, chunks, and facts into the repository layer.

## 2. Surface integration

- [x] 2.1 Replace seeded-corpus startup in the workbench pipeline with the processed runtime bootstrap.
- [x] 2.2 Update the evaluation runner and package CLI to reuse the same processed runtime.
- [x] 2.3 Add explicit startup errors for missing or invalid processed artifacts.

## 3. Validation

- [x] 3.1 Add fixture-backed loader tests covering valid, missing, and malformed processed artifacts.
- [x] 3.2 Add smoke validation that app, evaluation, and CLI execute against the same processed runtime.
