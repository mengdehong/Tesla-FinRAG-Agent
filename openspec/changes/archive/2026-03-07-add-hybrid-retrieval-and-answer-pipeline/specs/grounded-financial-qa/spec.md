## ADDED Requirements

### Requirement: Structured query planning
The question-answering pipeline SHALL convert a user question into a structured query plan that captures time constraints, target metrics, retrieval filters, and whether explicit calculation is required before answer generation.

#### Scenario: Parse a period-specific question
- **WHEN** a user asks a question that includes an explicit period such as "2022 Q3" or a cross-year comparison range
- **THEN** the system produces a query plan that records those time constraints for downstream retrieval and calculation

### Requirement: Hybrid evidence retrieval
The question-answering pipeline SHALL retrieve evidence by combining keyword-aware search, semantic similarity, and metadata filters over normalized filing chunks and fact records.

#### Scenario: Match an exact financial term
- **WHEN** a user question includes a specific financial term such as "Free Cash Flow"
- **THEN** the system preserves that term in lexical retrieval rather than relying on semantic similarity alone

#### Scenario: Apply hard period filters
- **WHEN** the query plan contains explicit period or form filters
- **THEN** the retrieval system restricts candidate evidence using those filters before answer assembly

### Requirement: Explicit financial calculation
The question-answering pipeline SHALL perform ranking, aggregation, and period-over-period numeric reasoning in a dedicated calculation step rather than relying on free-form language generation to compute results.

#### Scenario: Compute a quarterly comparison
- **WHEN** a user asks for the quarter with the highest or lowest value across a time range
- **THEN** the system computes the comparison from structured fact records and records the calculation steps in the answer payload

### Requirement: Grounded answer payload
The question-answering pipeline SHALL return an answer payload that includes answer text, citations, calculation steps when used, retrieval debug context, and a confidence or limitation signal.

#### Scenario: Return a traceable answer
- **WHEN** the system answers a complex financial question
- **THEN** the returned payload includes the supporting evidence references needed for UI display and failure analysis
