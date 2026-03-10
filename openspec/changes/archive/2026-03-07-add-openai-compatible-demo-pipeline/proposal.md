## Why

The current demo and workbench pipeline only supports a local deterministic path over the seeded demo corpus. The repository already hints at OpenAI-compatible configuration, but there is no explicit remote embedding plus chat demo path that can be smoke-tested from the CLI or used intentionally in the workbench.

## What Changes

- Add an explicit OpenAI-compatible provider layer for embeddings and grounded answer narration using the official `openai` Python SDK.
- Extend the demo/workbench runtime so operators can choose `local` or `openai-compatible` provider modes without changing the underlying demo corpus.
- Add a CLI smoke entrypoint for asking a single question and printing either a concise answer summary or a full `AnswerPayload` JSON document.
- Extend runtime diagnostics so answer payloads report provider, model, and vector-lane execution details.
- Fail fast when remote mode is requested without required credentials or when remote provider calls fail.

## Capabilities

### New Capabilities
- `openai-compatible-demo-pipeline`: Explicit remote provider support for the demo corpus, including embedding generation, chat answer narration, and CLI smoke execution.

### Modified Capabilities
- `demo-evaluation-workbench`: The demo runtime gains explicit provider mode selection and surfaces provider diagnostics in the answer payload.

## Impact

- Affected code: settings, provider modules, workbench pipeline wiring, package CLI, and debug payload generation.
- Affected APIs: runtime provider selection, CLI invocation shape, and `AnswerPayload.retrieval_debug` fields.
- Dependencies: add the official `openai` Python SDK.
- Systems: local demo flow, remote embedding/chat provider calls, and CLI smoke validation.
