# Onside FPL provider

This experiment adds **Onside** as an independent, read-only FPL evidence provider for the FPL Test project.

It does not replace, modify, average with, or silently override Dastan/SmartPlay or AIrsenal.

## Verified upstream boundary

The npm package `onside-football-mcp` is pinned for provenance at version `0.2.0` / upstream commit `cf48d1d3374768de5cb6f7716d7e76f06b16e0b6`.

At that commit the MCP exposes eight World Cup 2026 tools and **no FPL tool**. Therefore this experiment does not pretend that FPL xP comes through MCP. When/if the MCP adds a documented FPL tool, it may become an alternate transport only after the same schema, freshness, identity and provenance tests pass.

Current Onside FPL evidence is available through two distinct public surfaces and is intentionally kept distinct:

1. **Frozen audit ledger** — `https://onsidearena.com/fpl/predicted-points`. This is the pre-deadline projection record used for grading. The server-rendered HTML currently contains a top subset of the larger advertised board, so it is useful for provenance/discovery but is not treated as a complete live player universe by this adapter.
2. **Live player pages** — `https://onsidearena.com/player/<slug>-<official-fpl-id>`. These expose current-GW xP, six-Gameweek xP and Onside start probability for a selected current FPL player and can update as the live model refreshes.

Historical grading evidence is published at `https://onsidearena.com/data/graded-predictions.csv`.

## Adapters

### `onside_fpl.py` — frozen ledger parser

Produces `onside-fpl-evidence-v1` with:

- Official FPL target Gameweek and deadline;
- Onside engine version and frozen capture timestamp;
- source-response SHA-256;
- matched Official FPL element ids;
- frozen Onside current-GW xP, price and ownership where server-rendered;
- Official FPL availability/news for current-player sanity checks;
- excluded stale/unmatched rows;
- provenance and attribution.

It fails closed on wrong Gameweek, post-deadline capture, stale evidence, conflicting duplicate rows, malformed tables and poor identity coverage.

### `onside_live.py` — current selected-player evidence

Produces `onside-fpl-live-player-evidence-v1` for exact Official FPL element ids. For each validated current player it records:

- current Onside Gameweek xP;
- Onside next-six-Gameweek xP;
- Onside start probability;
- Onside engine/as-of labels when published;
- current Official FPL price/status/chance/news separately;
- exact source URL and response SHA-256.

The FPL ID embedded by Onside must equal the requested Official FPL element id. Players that no longer pass the current-player sanity gate are excluded. Onside model values and Official FPL status fields are never silently blended.

## Live usage

Selected players — preferred for recommendation evidence:

```bash
python experiments/onside_free/onside_live.py --elements 367,411 --out /tmp/onside.json
```

For an acceptance/discovery spot-check, omit `--elements` and the adapter discovers a small set from the server-rendered frozen board before retrieving their live pages:

```bash
python experiments/onside_free/onside_live.py --top 5 --out /tmp/onside.json
```

No login, API key or manager id is required. Both commands are read-only and never call an FPL write endpoint.

## Recommendation semantics

Onside is labelled separately:

1. live owner state — governed FPL owner-state surface, never Onside;
2. Dastan player evidence;
3. SmartPlay optimiser evidence;
4. AIrsenal evidence;
5. **Onside live evidence** for the owned players and legal candidate shortlist;
6. current football/news context;
7. ChatGPT interpretation and one final recommendation.

For transfer/waiver questions, Dastan/AIrsenal can identify the broad candidate set; Onside can independently score the owned player and those candidate element ids. This avoids hundreds of unnecessary requests while ensuring the actual decision comparison has Onside evidence when available.

Do not infer an Onside value that is not present. The frozen ledger and live player pages are different vintages and must not be mixed as though they were one snapshot.

## Accuracy claims

The provider links to Onside's graded dataset but does not hard-code a marketing MAE as trusted model truth. Cross-model MAE should be recomputed on identical rows, gameweeks and eligibility rules before it is used to rank providers.

## Attribution

Model projections: Onside — https://onsidearena.com/

Onside publishes its graded FPL dataset under CC-BY-4.0 and requests attribution to `onsidearena.com`.
