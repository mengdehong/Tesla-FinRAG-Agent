## MODIFIED Requirements

### Requirement: Explicit remote provider mode
The demo runtime SHALL support an explicit `openai-compatible` provider mode that uses configured OpenAI-compatible APIs for grounded answer narration while using the shared indexed embedding backend for query embeddings against the persisted LanceDB corpus.

#### Scenario: Run a remote demo query
- **WHEN** an operator invokes the demo runtime with `provider=openai-compatible` and valid provider credentials
- **THEN** the system calls the configured chat model to narrate the grounded answer text and uses the shared indexed embedding backend to query the persisted LanceDB vector store

### Requirement: Provider-aware diagnostics
The answer payload SHALL report provider and vector-lane diagnostics whenever the demo runtime executes in either provider mode.

#### Scenario: Inspect remote execution metadata
- **WHEN** a question is answered through the demo runtime
- **THEN** `AnswerPayload.retrieval_debug` includes the answer provider, the shared indexed embedding backend, selected models, and vector hit counts for that run
