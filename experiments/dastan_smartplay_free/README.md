# Dastan + SmartPlay Solver — £0 local prototype

This directory is an isolated experiment on branch `prototype/dastan-smartplay`.
It does **not** modify Apex Price Risk on `main`, FPL Apex, or FPL Apex Next.

## Goal

Prove that we can produce the current one-gameweek-ahead Dastan projection locally
from free public inputs and feed it directly to the open-source SmartPlay Solver,
without the paid SmartPlay hosted connector.

The upstream projects are used unmodified at exact commits:

- Dastan: `19376523afdec4836d0e6b5632c6773d0fe40c53`
- SmartPlay Solver: `7ec56e944982020f8709db5d00b0b78821fb1f38`

The adapter intentionally calls Dastan's own public source reconstruction,
feature-building and inference code. Our code only supplies the missing live boundary:
completed current-season FPL rows plus target-gameweek fixture rows.

## What the adapter does

1. Downloads/caches Dastan's pinned historical FPL/Understat inputs.
2. Reads current Official FPL bootstrap, fixtures and player histories.
3. Builds current-season completed rows and zero-target rows for the next GW.
4. Uses Dastan's own Understat fallback loader for mapped players and teams.
5. Calls Dastan's exact public `build_feature_frame` implementation.
6. Injects current pre-deadline FPL `ep_next`, status, chance-of-playing and news signals.
7. Runs Dastan's released model weights with `predictor.Dastan`.
8. Exports the shortest SmartPlay Solver projection contract:
   `element,gameweek,xpts,expected_minutes`.
9. Optionally compares a small manually captured SmartPlay reference set.

No SmartPlay website scraping is performed.

## First acceptance run: GW4

On the Mac:

```bash
cd /path/to/test
git fetch origin
git switch prototype/dastan-smartplay
cd experiments/dastan_smartplay_free

bash bootstrap.sh

.venv/bin/python live_gw.py \
  --gameweek 4 \
  --dastan-repo .vendor/smartplayfpl-dastan \
  --work-dir "$HOME/.cache/dastan-smartplay-free" \
  --output-dir output \
  --reference smartplay_gw4_spotcheck.csv
```

The first run downloads public historical/model dependencies and live Understat data.
Later runs reuse the local cache except that current Understat player snapshots are
refreshed deliberately.

Expected outputs:

- `output/dastan_gw4_fixtures.csv` — fixture-level Dastan result.
- `output/dastan_gw4_solver.csv` — SmartPlay Solver-ready projection file.
- `output/dastan_gw4_acceptance.json` — provenance and reference comparison.

## Acceptance rule

This prototype is **not accepted merely because it executes**.

For GW4, inspect the five manual SmartPlay reference players in
`smartplay_gw4_spotcheck.csv`. The acceptance JSON reports exact deltas and MAE.
We expect small differences if the public release and hosted production pipeline are
materially aligned. Large systematic differences mean the live adapter is still
missing production context and must not be presented as SmartPlay parity.

The five public values were manually observed on 6 September 2026. They are a small
spot check, not a copied dataset and not an automated feed.

## Important scientific boundary

Dastan's published validation is one-gameweek-ahead. This prototype therefore starts
with **GW+1 only**. Do not create multi-GW SmartPlay Solver plans by repeating or
inventing the same xPts into future GWs. Multi-horizon support should only be added
once future-fixture feature generation is separately validated.

## Cost boundary

The intended runtime is the existing Mac. The inputs and software used here are public
and the local optimiser is HiGHS. This prototype does not require paid APIs, a hosted
SmartPlay account, GitHub Actions, a cloud runner or another daemon.
