## ADDED Requirements

### Requirement: Query metric mentions SHALL resolve through a shared concept catalog
The system SHALL build a searchable concept catalog from Tesla XBRL/companyfacts metadata and SHALL use that catalog to resolve user metric mentions before retrieval depends on planner-owned aliases alone.

#### Scenario: Exact label resolution
- **WHEN** the user asks for a metric whose label or alias exactly matches a catalog entry
- **THEN** the planner SHALL accept that concept directly and record the resolution method as exact

#### Scenario: Conservative semantic review
- **WHEN** the resolver only has semantic candidates and the current embedding backend is uncalibrated
- **THEN** the resolver SHALL keep the mention unresolved, surface candidates diagnostically, and SHALL NOT silently promote the top semantic match into a required concept

### Requirement: Semantic acceptance policy SHALL be model-calibrated
The system SHALL treat semantic acceptance thresholds as model-calibrated defaults tied to the current embedding backend rather than as universal constants.

#### Scenario: Embedding backend changes
- **WHEN** the embedding backend or similarity model changes
- **THEN** operator-visible diagnostics SHALL make clear that any absolute acceptance score/gap defaults require recalibration before they are trusted in strict acceptance mode
