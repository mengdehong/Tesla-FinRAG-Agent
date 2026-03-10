## ADDED Requirements

### Requirement: Supported local runtime bootstrap path
The project SHALL define a single supported local bootstrap path that takes an operator from a fresh clone to a runnable Tesla FinRAG runtime using documented commands for dependency installation, processed-corpus preparation, and runtime launch.

#### Scenario: Bootstrap from a fresh clone
- **WHEN** a developer starts from a fresh repository checkout without an existing local environment
- **THEN** the project provides an ordered command path that covers environment setup, processed-corpus preparation, and launch of at least one runtime surface

### Requirement: Explicit processed-data readiness step
The supported bootstrap path SHALL require operators to verify or generate `data/processed` before relying on the runtime surfaces for question answering.

#### Scenario: Processed artifacts are not ready
- **WHEN** an operator follows the documented bootstrap path and `data/processed` is absent or incomplete
- **THEN** the workflow directs the operator to the supported ingestion command instead of assuming the runtime can proceed

