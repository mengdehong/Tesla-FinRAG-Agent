## Why

The current planner still depends on a static alias dictionary for metric detection. That works for a narrow benchmark set, but Tesla's `companyfacts.json` already contains hundreds of standard XBRL concepts with official labels and descriptions, and the system needs a shared semantic path for resolving user metric mentions beyond hand-written aliases.

## What Changes

- Add a searchable concept catalog built from Tesla `companyfacts.json` plus curated custom concepts.
- Add a conservative semantic concept resolver that supports exact, lexical, safe-equivalent, and semantic candidate resolution.
- Make planner and runtime diagnostics surface concept-resolution details and calibration metadata.
- Document that semantic acceptance thresholds are model-calibrated defaults and must be recalibrated when the embedding backend changes.

## Capabilities

### New Capabilities
- `xbrl-concept-resolution`: Build and query a lightweight concept catalog for resolving user financial metric mentions.

### Modified Capabilities
- `grounded-financial-qa`: Query planning now uses shared concept-resolution diagnostics instead of a planner-owned alias table alone.

## Impact

- Adds a new `tesla_finrag.concepts` module.
- Extends settings with concept search and calibration controls.
- Changes planner/runtime diagnostics so evaluation and operators can see how concepts were resolved.
