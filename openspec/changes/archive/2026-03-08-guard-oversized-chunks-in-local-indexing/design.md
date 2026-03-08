## Context

The processed corpus currently stores normalized section chunks and table chunks
as stable JSON artifacts, then builds a LanceDB index by embedding each chunk's
full text verbatim. That works only when every chunk fits inside the configured
embedding model's context window. With local Ollama embeddings, some extracted
tables are larger than the backend accepts, so ingestion fails mid-index-build
with a `400 input length exceeds the context length` error.

This is a cross-cutting problem because changing the indexing strategy also
changes LanceDB row shape, retrieval semantics, and runtime validation. The
project already depends on processed chunk files for citations and UI display,
so the fix must preserve those chunk artifacts as the canonical source records.

## Goals / Non-Goals

**Goals:**
- Make LanceDB index generation robust when normalized chunks exceed the active
  embedding backend's practical input budget.
- Keep processed section/table chunk artifacts stable so citations, answer
  display, and operator debugging still point to the original chunk records.
- Allow runtime bootstrap to load a segmented LanceDB index without treating the
  higher vector row count as corruption.
- Preserve retrieval quality by ranking on smaller semantic segments while
  mapping results back to the original chunk units used elsewhere in the
  pipeline.
- Surface actionable failures when a chunk still cannot be embedded after the
  new guard path is applied.

**Non-Goals:**
- Rewriting the filing normalization pipeline to emit permanently smaller chunk
  JSON artifacts.
- Introducing model-specific tokenizers or a dependency on backend-native token
  counting libraries.
- Changing the answer payload format, citation model, or XBRL fact pipeline.
- Supporting every possible embedding backend limit perfectly in one step; the
  design targets the current local Ollama path first.

## Decisions

### Decision: Segment only at the indexing boundary
Oversized inputs will be segmented when building LanceDB rows, not when writing
processed chunk JSON files. The processed corpus remains chunk-centric, while
the vector index becomes segment-centric.

Alternative considered: rewrite the normalization chunkers to emit smaller
processed chunks. Rejected because it would ripple into citations, fixture
expectations, and retrieval/debug UX for a problem that exists specifically at
the embedding boundary.

### Decision: Use structure-aware segmentation with a conservative text budget
Narrative chunks will be segmented on paragraph or sentence boundaries where
possible, and table chunks will be segmented on row/line boundaries while
repeating minimal header context when needed. Both paths will enforce a
conservative character budget with overlap and a hard fallback split so the
embedding backend never receives an arbitrarily long raw string.

Alternative considered: reduce batch size or truncate each oversized input.
Reducing batch size does not fix per-input context errors, and truncation would
silently drop potentially important table rows.

### Decision: Persist lineage metadata per vector row
Each LanceDB row will carry enough metadata to map a segment back to its source
chunk, such as original chunk id, source doc id, segment ordinal, and segment
count. Retrieval can then score segmented rows but dedupe and hydrate evidence
at the original chunk level.

Alternative considered: store only segmented text and rely on lexical matching
to recover the original chunk. Rejected because it makes runtime validation and
citation reconstruction brittle.

### Decision: Replace count-equality validation with lineage-aware validation
Runtime bootstrap will stop assuming that LanceDB row count must equal processed
chunk count. Instead it will validate that:
- the index embedding model still matches configuration,
- every indexed source chunk id exists in the processed corpus,
- the index metadata records both source chunk count and vector row count.

Alternative considered: keep the current 1:1 count check and disable
segmentation outside local mode. Rejected because it would make the ingestion
contract depend on provider choice and keep local indexing fragile.

### Decision: Fail loudly only when segmentation cannot make a chunk indexable
If a chunk still cannot be embedded after structure-aware splitting and hard
fallback splitting, ingestion will fail with operator-facing diagnostics that
identify the filing, chunk path, chunk type, and text length. This keeps failure
analysis targeted instead of surfacing a generic provider error with no source
identity.

Alternative considered: skip unindexable chunks and continue. Rejected because
it would silently degrade retrieval coverage and grounded-answer completeness.

## Risks / Trade-offs

- [Risk] Segmenting large tables may produce more LanceDB rows and increase
  indexing/storage cost. -> Mitigation: keep segment budgets conservative,
  dedupe retrieval results at the source chunk level, and validate on realistic
  corpora.
- [Risk] Repeating header context across table segments may bias similarity
  scores. -> Mitigation: use small repeated headers only where needed and keep
  lexical retrieval plus source-chunk deduping in the hybrid path.
- [Risk] Character-based segmentation is only a proxy for token limits. ->
  Mitigation: choose a conservative default budget and keep explicit failures if
  a backend still rejects a segment.
- [Risk] Runtime validation becomes more complex than a simple count check. ->
  Mitigation: persist explicit metadata fields and add targeted validation tests
  for segmented indexes and orphaned lineage.

## Migration Plan

1. Define the spec deltas for ingestion and runtime segmented-index behavior.
2. Implement index-time segmentation plus lineage metadata in the LanceDB row
   schema and ingestion builder.
3. Update retrieval/runtime bootstrap to validate and consume segmented indexes.
4. Rebuild `data/processed/lancedb` through the supported ingestion command.
5. Run regression tests for oversized chunk indexing, runtime bootstrap, and
   local Ollama end-to-end ingestion.

## Open Questions

- None. The proposal intentionally fixes the strategy around segmentation,
  lineage metadata, and runtime validation instead of leaving the indexing
  fallback ambiguous.
