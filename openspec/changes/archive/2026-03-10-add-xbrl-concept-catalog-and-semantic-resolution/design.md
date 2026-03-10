## Context

The current repo already stores Tesla SEC facts in `companyfacts.json` and uses typed `QueryPlan`/`AnswerPayload` contracts. The missing piece is a shared semantic mapping layer between user mentions and XBRL concepts.

## Goals / Non-Goals

**Goals:**
- Build a reusable concept catalog from official XBRL metadata.
- Support exact, lexical, semantic, and safe-equivalent resolution.
- Keep semantic acceptance conservative unless the current embedding backend has been calibrated.

**Non-Goals:**
- Introduce a second vector database beyond the repo's local-first stack.
- Let semantic candidates silently replace required concepts without diagnostics.

## Decisions

- Build the catalog from `data/raw/companyfacts.json` and keep it lightweight in Python models.
- Preserve curated aliases for high-value fast paths, but make them supplemental instead of the primary coverage strategy.
- Treat semantic score cutoffs as backend-specific defaults. The resolver exposes calibration metadata so operators know when a threshold is stale after a model swap.
- Return candidate lists for unresolved mentions so later agent repairs can choose alternatives explicitly.

## Risks / Trade-offs

- [Risk] Uncalibrated semantic scores may look confident while being wrong. -> Mitigation: conservative mode blocks hard acceptance when the backend has not been calibrated.
- [Risk] Equivalent accounting concepts can drift semantically. -> Mitigation: keep safe-equivalent mappings explicit and small.
- [Risk] Catalog generation could drift from planner labels. -> Mitigation: share concept-resolution output as planner diagnostics.
