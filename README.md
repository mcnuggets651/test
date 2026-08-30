# Apex Price Risk

A standalone, non-serving Fantasy Premier League price-change forecasting and transfer-route fragility system.

## Safety first

This project is **not part of the Apex V2 decision engine**. It has no runtime dependency on FPL Apex, never changes player xP, never changes the canonical transfer optimiser, never authenticates to a manager account, and cannot authorize a transfer or an early move.

Hard-coded boundary:

- `serving_authorized = false`
- `production_influence = NONE`
- no imports from Apex/FPL Apex packages
- no checkout or call into the FPL Apex repository
- public Official FPL data only
- failure here has zero effect on FPL Apex production

The current GitHub repository shell is named `test` because the connected GitHub action surface can initialize an existing repository but cannot rename or create a repository. The product identity is **Apex Price Risk**. Renaming the repository to `apex-price-risk` later is cosmetic and does not change its independent repository boundary.

## What it does

Every three hours the collector:

1. downloads the public Official FPL `bootstrap-static` payload;
2. captures current player price, ownership, transfer activity, price-change counters and availability state;
3. writes a deterministic compressed point-in-time snapshot;
4. generates a sealed 24-hour rise/fall forecast using the latest shadow model, or a deliberately low-confidence cold-start baseline;
5. appends the snapshot and forecast to the separate `observations` branch.

Daily evaluation:

1. builds labels only from future snapshots whose 24-hour horizon has elapsed;
2. trains rise and fall models with chronological train/calibration/test partitions;
3. applies probability calibration;
4. evaluates Brier score, log loss, average precision and calibration;
5. scores previously sealed forecasts prospectively;
6. appends model/evaluation artifacts to `observations`.

No retrospective regeneration of forecasts is used for prospective scoring.

## Official price-change page

The human-facing Official FPL price-change page is:

`https://fantasy.premierleague.com/en/price-changes`

For machine acquisition, the canonical source is the public Official FPL bootstrap endpoint because it exposes current `now_cost`, `cost_change_event`, `cost_change_start`, ownership, transfer activity and player status in a stable structured payload. Confirmed price moves are labelled from observed changes in `now_cost` between point-in-time snapshots. The UI page remains a useful human parity/audit surface; the system does not scrape the React page or infer unpublished FPL thresholds from it.

## Branches

- `main`: code, tests, workflows and documentation only.
- `observations`: machine-written public point-in-time snapshots, sealed forecasts, trained shadow model artifacts and prospective evaluation reports.

The data branch is intentionally separate so routine evidence collection does not mutate the model/code branch.

## Commands

- `apex-price-risk capture`
- `apex-price-risk forecast`
- `apex-price-risk train`
- `apex-price-risk evaluate`
- `apex-price-risk route-advisory`
- `apex-price-risk architecture-check`

`route-advisory` reports probability bounds for a planned route becoming fragile when specified target players rise. It deliberately avoids assuming independence between price moves and always outputs `action_authorized=false`.

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/DATA_CONTRACT.md`
- `docs/MODEL.md`
- `docs/GOVERNANCE.md`
- `docs/OPERATIONS.md`
- `docs/INTEGRATION_BOUNDARY.md`
- `docs/DECISIONS.md`

## Status

**V1: shadow/data-collection system.** It is useful immediately for building the prospective dataset and measuring whether price-change prediction is actually possible with sufficient calibration. It is intentionally not authorized to influence Apex decisions yet.
