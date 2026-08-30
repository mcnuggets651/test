# Architecture Decisions

## ADR-001 — Separate repository boundary

Accepted. Price-risk research must not live in the frozen FPL Apex production repository.

## ADR-002 — Official-only V1 acquisition

Accepted. Use structured public Official FPL bootstrap data. Do not scrape the human price-change React page for machine truth.

## ADR-003 — Predict probability, not secret threshold

Accepted. The FPL pricing algorithm is not fully public. V1 estimates calibrated rise/fall probabilities from observed data rather than claiming to reproduce an unpublished formula.

## ADR-004 — Timestamped prospective sealing

Accepted. Every forecast is stored before the outcome and includes source/code/model identity.

## ADR-005 — Separate observations branch

Accepted. Routine data collection must not create code commits on `main`.

## ADR-006 — Logistic baseline before complexity

Accepted. Establish an interpretable calibrated benchmark. More complex challengers must beat it prospectively.

## ADR-007 — No independence assumption for route targets

Accepted. Multi-target route diagnostics report probability bounds, not multiplied marginals.

## ADR-008 — No automatic production promotion

Accepted. Prospective model quality can justify a review, never self-authorize integration.

## ADR-009 — Price signal never changes xP

Permanent. Price is an execution/budget risk, not a football points projection.

## ADR-010 — Interval-censored boundary labels

Accepted. A move first observed only after the 24-hour target is excluded if its exact timing across the boundary is unknown. The observation grace interval confirms label maturity; it does not extend the prediction target.

## ADR-011 — Purged chronological model evaluation

Accepted. Training, calibration and test feature-time regions are separated by 27-hour purge gaps so a 24-hour outcome label plus observation grace cannot leak across adjacent model partitions.
