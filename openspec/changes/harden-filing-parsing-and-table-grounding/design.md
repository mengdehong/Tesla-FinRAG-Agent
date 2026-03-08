## Context

The repository already normalizes filing narrative, table chunks, and XBRL facts into a processed corpus, but the PDF parsing path still treats extracted table content as implicitly trustworthy once it passes basic shape checks. This leaves a quality gap between authoritative facts and table-derived evidence, especially when PDF extraction corrupts numeric cells, loses captions, or requires parser fallback behavior that is not surfaced to operators.

## Goals / Non-Goals

**Goals:**
- Make parser provenance and extraction fallback behavior visible in processed artifacts and ingestion diagnostics.
- Validate table-like numeric output before it is treated as grounded evidence.
- Reconcile table-derived financial values against authoritative XBRL facts when a reliable concept-and-period match exists.
- Preserve complete table units and provenance so downstream citations remain traceable.

**Non-Goals:**
- Rebuild the entire ingestion architecture around a cloud parsing service.
- Replace XBRL facts as the primary authority for core financial numbers.
- Redesign retrieval or answer-generation behavior beyond the ingestion-quality signals this change produces.

## Decisions

### 1. Keep the local parser path as the default, but make parser fallback explicit
The default ingestion path will remain local-first, with the current parser stack as the primary path and an optional local fallback path for pages where the primary parser fails or returns clearly incomplete output. This keeps the workflow reproducible and offline-friendly while still allowing targeted reliability improvements.

Alternative considered: making a cloud parser mandatory for all filings. Rejected because it would add cost, credentials, and a non-local dependency to a repository that currently optimizes for local reproducibility.

### 2. Add validation metadata to normalized table artifacts
Normalized table output should carry parser provenance and validation metadata in addition to the raw extracted content. The metadata should distinguish at least: parser path used, fallback usage, numeric validation outcome, and authoritative fact reconciliation outcome when applicable.

Alternative considered: leaving validation results only in logs. Rejected because downstream failure analysis and citation review need artifact-level traceability after the ingestion run has finished.

### 3. Validate numeric cells before treating them as trusted table evidence
Table extraction will run a numeric normalization pass over cells that look like financial values. Cells that fail parsing, contain suspicious OCR substitutions, or conflict with authoritative facts beyond a configured tolerance will be marked as untrusted instead of silently entering the corpus as valid evidence.

Alternative considered: relying only on XBRL facts for numeric questions and ignoring table quality. Rejected because table output still matters for citations, text-plus-table questions, and operator debugging.

### 4. Reconcile only where concept-and-period matching is explicit
Authoritative fact reconciliation should only occur when the ingestion flow can map a table value to a known metric and period with acceptable confidence. This avoids overfitting brittle heuristics to arbitrary table layouts while still catching the most damaging numeric corruption cases in core statement tables.

Alternative considered: attempting full semantic reconciliation for every cell in every table. Rejected because the mapping logic would be expensive, fragile, and hard to validate incrementally.

## Risks / Trade-offs

- [Risk] Additional parser fallback and validation work increases ingestion latency. → Mitigation: run validation only on table-like numeric cells and preserve incremental reuse so unchanged filings are not reparsed.
- [Risk] Fact-versus-table mismatch detection can produce false positives due to scaling or presentation differences. → Mitigation: compare normalized values only after unit and scale alignment, and record mismatches as diagnostics rather than hard failures unless the artifact is unusable.
- [Risk] Adding metadata to processed artifacts can ripple into runtime loaders and tests. → Mitigation: keep new fields backward-compatible and typed, and extend loaders/tests in the same change.

## Migration Plan

- Extend processed artifact models and writers to include parser and validation metadata.
- Re-run ingestion to regenerate processed tables and diagnostics under the updated schema.
- Refresh ingestion and runtime tests against the regenerated artifacts.

## Open Questions

- Whether a confidence score is needed in addition to categorical validation states can be decided during implementation if the typed metadata remains stable.

## Operator Guidance

### Pipeline diagnostics output

After a successful `run_pipeline()` call, the summary dict now includes an `ingestion_diagnostics` key:

```python
{
    "ingestion_diagnostics": {
        "fallback_pages": 2,     # pages where PyMuPDF replaced pdfplumber
        "failed_pages": 1,       # pages where no parser produced usable output
        "validation_failed_tables": 0,  # tables with at least one unparseable numeric cell
        "validation_suspect_tables": 3, # tables with OCR-suspicious cells
    }
}
```

### Interpreting table validation status

Each `TableChunk` now carries a `validation_status` field with one of four values:

| Status | Meaning | Action |
|---|---|---|
| `valid` | All numeric cells parsed successfully | Safe to cite |
| `suspect` | Some cells show OCR corruption patterns | Review before citing in calculations |
| `failed` | At least one numeric cell could not be normalized | Do not use for numeric answers |
| `not_checked` | Table had no numeric-looking cells | N/A |

### Parser fallback tracking

Each chunk (`SectionChunk` or `TableChunk`) has an optional `parser_provenance` field. When present:

- `parser_name`: which parser produced the artifact (`"pdfplumber"` or `"pymupdf"`)
- `used_fallback`: `true` if the primary parser failed and a fallback was used
- `fallback_reason`: why fallback was triggered (`"empty_text"`, `"insufficient_text"`)

### Fact reconciliation

`TableChunk.fact_reconciliations` records results when table values are compared against authoritative XBRL facts. Each entry shows:
- The XBRL concept and period matched
- The table-derived vs. authoritative value
- Whether they agree within the configured tolerance (default 1%)

Reconciliation only runs when column headers contain a matching concept label — it is intentionally conservative to avoid false positives.

### Installing the fallback parser

The PyMuPDF fallback parser is an **optional dependency**. Without it, the
`_HAS_PYMUPDF` flag in `analysis.py` is `False` and pages that need fallback
will be reported as `no_fallback_available` in diagnostics.

To enable the fallback:

```bash
# Using uv
uv add pymupdf

# Or install the optional group
pip install ".[fallback]"
```
