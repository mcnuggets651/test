# Onside FPL provider

This experiment adds **Onside** as an independent, read-only FPL evidence provider for the FPL Test project.

It does not replace, modify, average with, or silently override Dastan/SmartPlay or AIrsenal.

## Verified upstream boundary

The npm package `onside-football-mcp` is pinned for provenance at version `0.2.0` / upstream commit `cf48d1d3374768de5cb6f7716d7e76f06b16e0b6`.

At that commit the MCP exposes eight World Cup 2026 tools and **no FPL tool**. Therefore this experiment does not pretend that FPL xP comes through MCP. Current FPL evidence is read from Onside's public frozen projection page:

`https://onsidearena.com/fpl/predicted-points`

Historical grading evidence is published by Onside at:

`https://onsidearena.com/data/graded-predictions.csv`

When/if the MCP adds a documented FPL tool, it can become an alternate transport only after the same schema, freshness, identity and provenance tests pass.

## What the adapter produces

`onside_fpl.py` creates `onside-fpl-evidence-v1` JSON containing:

- current Official FPL target Gameweek and deadline;
- Onside engine version and frozen capture timestamp;
- source-response SHA-256;
- matched Official FPL element ids;
- Onside current-GW xP, price and ownership where published;
- Official FPL availability/news fields for identity/current-player sanity checks;
- unmatched/stale upstream rows, excluded from usable model evidence;
- provenance and attribution metadata;
- explicit limitations.

The adapter is fail-closed. It rejects:

- a wrong Gameweek;
- an Onside capture after the Official FPL deadline;
- excessively old captures;
- malformed/duplicate-conflicting rows;
- implausibly small source tables;
- poor identity-match coverage against current Official FPL.

## Live usage

```bash
python experiments/onside_free/onside_fpl.py --out /tmp/onside.json
```

No login, API key or manager id is required. The command is read-only and never calls an FPL write endpoint.

## Evidence semantics

Onside evidence is labelled separately in recommendations:

1. live owner state — from the governed FPL owner-state surface, never Onside;
2. Dastan evidence — unchanged;
3. SmartPlay optimiser evidence — unchanged;
4. AIrsenal evidence — unchanged;
5. **Onside evidence — current-GW xP only unless a future validated surface exposes more**;
6. current football/news context;
7. ChatGPT interpretation and final recommendation.

Do not infer Onside minutes, start probability, multi-GW xP or optimiser edges from this adapter.

## Accuracy claims

The provider carries a link to Onside's graded dataset but does not hard-code a marketing MAE as trusted model truth. Cross-model MAE should be recomputed on identical rows/cutoffs before it is used to rank providers.

## Attribution

Model projections: Onside — https://onsidearena.com/

Onside publishes its graded FPL dataset under CC-BY-4.0 and requests attribution to `onsidearena.com`.
