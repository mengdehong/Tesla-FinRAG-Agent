## MODIFIED Requirements

### Requirement: Structured query planning
The question-answering pipeline SHALL convert a user question into a structured query plan that captures time constraints, target metrics, retrieval filters, required period semantics, decomposed sub-questions when needed, and whether explicit calculation is required before answer generation. The planning service SHALL support English questions, Simplified Chinese questions, and mixed-language questions over the same Tesla filing corpus, and SHALL preserve both the original user query and a normalized search representation for downstream retrieval.

#### Scenario: Parse a period-specific English question
- **WHEN** a user asks a question that includes an explicit period such as "2022 Q3" or a cross-year comparison range
- **THEN** the system produces a query plan that records those time constraints and period semantics for downstream retrieval and calculation

#### Scenario: Parse a period-specific Chinese question
- **WHEN** a user asks a Chinese question that includes an explicit period such as "2023财年", "2023年Q3", "2023年第三季度", or "截至2023年12月31日"
- **THEN** the system produces the same structured period constraints and period semantics that an equivalent English question would produce

#### Scenario: Decompose a multi-period comparison
- **WHEN** a user asks a comparison, ranking, or cross-period question that requires evidence from multiple periods
- **THEN** the system emits period-aware sub-questions or retrieval units so each required period can be retrieved and validated before final answer assembly

#### Scenario: Normalize a Chinese financial metric for retrieval
- **WHEN** a user asks a Chinese or mixed-language question containing a financial term such as "总营收", "毛利率", "营业利润率", or "现金及现金等价物"
- **THEN** the query plan resolves the canonical required concepts and also records normalized retrieval text that aligns those terms with the English filing corpus

### Requirement: Hybrid evidence retrieval
The question-answering pipeline SHALL retrieve evidence by combining keyword-aware search, semantic similarity from the persisted LanceDB vector index, and metadata filters over normalized filing chunks and fact records. When the query plan contains multiple required periods or decomposed sub-questions, the retrieval flow SHALL apply hard scope constraints for each retrieval unit before merging evidence into the final bundle. The retrieval flow SHALL consume the normalized search text from the query plan so Chinese or mixed-language user questions can still retrieve relevant evidence from the primarily English filing corpus.

#### Scenario: Match an exact financial term
- **WHEN** a user question includes a specific financial term such as "Free Cash Flow"
- **THEN** the system preserves that term in lexical retrieval rather than relying on semantic similarity alone

#### Scenario: Match a Chinese financial term against English filings
- **WHEN** a user asks a Chinese question containing a term such as "自由现金流" or "供应链风险"
- **THEN** the retrieval system uses normalized lexical or semantic search text that can match the equivalent English filing evidence instead of depending on the raw Chinese query text alone

#### Scenario: Apply hard period filters
- **WHEN** the query plan contains explicit period or form filters
- **THEN** the retrieval system restricts candidate evidence using those filters before answer assembly

#### Scenario: Use the persisted vector index
- **WHEN** the query pipeline executes semantic retrieval for a processed corpus that includes a LanceDB index
- **THEN** the vector lane reads from the persisted LanceDB store instead of rebuilding an in-memory corpus index for that process

#### Scenario: Retrieve evidence for each required period
- **WHEN** a query requires evidence from more than one period or comparison leg
- **THEN** the retrieval flow preserves coverage for each required period instead of returning only the highest-scoring subset from a single fused pass

### Requirement: Grounded answer payload
The question-answering pipeline SHALL return an answer payload that includes answer text, citations, calculation steps when used, retrieval debug context, and a confidence or limitation signal. When the required grounded evidence is missing, incomplete, or semantically incompatible with the requested question, the payload SHALL return a limitation status rather than a speculative answer. The answer payload SHALL adapt its user-facing summary and limitation wording to the detected language of the user question while preserving citation excerpts in the source filing language.

#### Scenario: Return a traceable answer
- **WHEN** the system answers a complex financial question
- **THEN** the returned payload includes the supporting evidence references needed for UI display and failure analysis

#### Scenario: Return a language-adaptive answer
- **WHEN** a user asks a Chinese or mixed-language question and the system has sufficient grounded evidence
- **THEN** the answer payload uses Chinese user-facing summary wording while keeping citations grounded to the original filing excerpts

#### Scenario: Return an evidence-based limitation
- **WHEN** the system cannot assemble the grounded facts or chunks required to answer a question reliably
- **THEN** the answer payload reports a limitation status and includes debug context explaining the missing or incompatible evidence

#### Scenario: Return a language-adaptive limitation
- **WHEN** a user asks a Chinese or mixed-language question and the system cannot assemble the required grounded evidence
- **THEN** the limitation message is presented in Chinese while the retrieval debug context still records the original and normalized query representations
