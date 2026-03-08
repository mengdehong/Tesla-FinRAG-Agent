## MODIFIED Requirements

### Requirement: Hybrid evidence retrieval
The question-answering pipeline SHALL retrieve evidence by combining keyword-aware search, semantic similarity from the persisted LanceDB vector index, and metadata filters over normalized filing chunks and fact records.

#### Scenario: Match an exact financial term
- **WHEN** a user question includes a specific financial term such as "Free Cash Flow"
- **THEN** the system preserves that term in lexical retrieval rather than relying on semantic similarity alone

#### Scenario: Apply hard period filters
- **WHEN** the query plan contains explicit period or form filters
- **THEN** the retrieval system restricts candidate evidence using those filters before answer assembly

#### Scenario: Use the persisted vector index
- **WHEN** the query pipeline executes semantic retrieval for a processed corpus that includes a LanceDB index
- **THEN** the vector lane reads from the persisted LanceDB store instead of rebuilding an in-memory corpus index for that process
