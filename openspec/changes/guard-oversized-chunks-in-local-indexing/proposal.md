## Why

The local LanceDB indexing path currently sends each normalized section or table
chunk to the embedding backend as a single input. When a persisted chunk is
longer than the Ollama embedding model can accept, ingestion aborts and the
processed corpus cannot be refreshed even though the underlying filing data is
otherwise valid.

## What Changes

- Add an indexing-safe segmentation strategy that breaks oversized normalized
  chunks into multiple vector rows before embedding, without mutating the
  processed chunk artifacts used for citations and answer display.
- Preserve lineage metadata from every segmented vector row back to its source
  section or table chunk so retrieval can rank by segment while still returning
  the original chunk record.
- Update processed-corpus runtime validation so segmented LanceDB indexes are
  treated as valid processed artifacts when their lineage metadata is complete
  and consistent.
- Surface explicit operator diagnostics when a chunk still cannot be indexed
  after segmentation, including enough identity information to find the source
  filing and chunk.
- Add regression coverage for oversized table and narrative chunks, segmented
  index reloads, and local Ollama ingestion against constrained embedding
  models.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `filing-ingestion`: LanceDB index generation must tolerate oversized
  normalized chunks by segmenting them into embedding-safe vector rows while
  preserving source traceability.
- `processed-corpus-runtime`: Runtime bootstrap and validation must accept a
  segmented LanceDB index whose vector row count can exceed the processed chunk
  count when lineage metadata remains consistent.

## Impact

- Affected code: ingestion pipeline, LanceDB retrieval store schema, runtime
  bootstrap validation, hybrid retrieval deduping, and operator-facing
  diagnostics.
- Affected data: LanceDB row cardinality and metadata will change from
  chunk-level only to segment-level with source-chunk lineage.
- Affected operators: local Ollama indexing should stop failing on oversized
  extracted tables, but a full re-ingest will be required to rebuild the index
  under the new schema.
