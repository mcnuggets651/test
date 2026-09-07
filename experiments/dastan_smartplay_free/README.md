# Dastan + SmartPlay Solver — £0 local strategy engine

This directory is an isolated experiment on branch `prototype/dastan-smartplay`.
It does **not** modify Apex Price Risk on `main`, FPL Apex, or FPL Apex Next.

## Status

The open GW+1 engine is operational and independently reproducible from public inputs.
The acceptance workflow has already demonstrated a complete GW4 run with:

- 654 Official-FPL player rows accepted by SmartPlay Solver;
- Dastan public artifact contract verified;
- current Coventry/Hull Understat mappings verified;
- exact released Dastan weights used for inference;
- SmartPlay Solver projection validation passed;
- all adapter/acceptance tests green.

The hosted SmartPlay service applies an unpublished early-season serving overlay during
GW1–5. Therefore the five manually observed hosted values are **diagnostic only** in
that period. The open engine is not described as exact hosted SmartPlay parity during
GW1–5.

## Frozen upstream inputs

The upstream projects are consumed at exact commits:

- Dastan: `19376523afdec4836d0e6b5632c6773d0fe40c53`
- SmartPlay Solver: `7ec56e944982020f8709db5d00b0b78821fb1f38`
- SmartPlay public mappings: `9b5bec6ae12541be24decd980e119af90617a868`

`bootstrap_v2.sh` verifies those pins before the strategy runner is allowed to continue.

## Architecture

`Official FPL + Understat -> live adapter -> Dastan feature builder/weights ->
SmartPlay Solver -> one recommendation`

The adapter does not retrain Dastan and does not implement a second model. It uses
Dastan's own public reconstruction, feature-building and inference code, with the thin
current-season identity/fixture boundary required to serve the live season.

No SmartPlay website scraping is performed.

## One-command strategy runner

The permanent entry point is `strategy.py`. It always discovers the live `is_next`
Official FPL gameweek, generates/reuses a fresh Dastan GW+1 projection, checks the
acceptance contract, validates the SmartPlay projection schema and then solves.

For the certified one-GW solve the runner explicitly passes
`--min-expected-minutes 1`. The pinned Solver internally treats a zero value as its
legacy default of 100 minutes, which would wrongly suppress most GW+1 transfer
candidates. A one-minute floor keeps the full viable player pool while excluding
literal zero-minute candidates; SmartPlay's existing sub-30-minute EV scaling remains
active. A regression test locks this behavior.

### Preferred: exact private Apex owner snapshot

Use the `strategy_snapshot.json` produced by the existing read-only private query
bridge. The file remains local and must not be committed:

```bash
cd experiments/dastan_smartplay_free
python strategy.py \
  --apex-strategy-snapshot /private/path/strategy_snapshot.json
```

The runner requires the snapshot to be:

- `PRIVATE_MANAGER` attested;
- immutable;
- transfer-complete;
- exactly 15 unique players;
- complete for purchase prices, selling prices, bank and free transfers;
- targeted at the exact current Official-FPL `is_next` gameweek;
- free of an active chip (the no-chip GW+1 runner fails closed rather than ignoring one).

It converts that state to SmartPlay's team schema in a mode-`0600` temporary file,
uses it for the solve, and deletes the temporary file afterwards. The strategy manifest
records only provenance and SHA-256 bindings, not the 15-player squad.

### Native SmartPlay team JSON

An exact local SmartPlay team file is also supported:

```bash
python strategy.py --team-file /private/path/team.json
```

The file must contain exactly 15 unique `picks` and exact `transfers.bank` and
`transfers.limit` values. Purchase and selling prices are mandatory.

### Public FPL entry fallback

Public FPL deliberately hides transfers made after the most recent deadline and does
not expose the current FT counter. Public entry mode therefore fails closed unless the
caller explicitly confirms that the revealed squad is still current and supplies FT
and bank:

```bash
python strategy.py \
  --entry-id 63984 \
  --public-state-is-current \
  --free-transfers 2 \
  --bank 0.5
```

Do not use that mode after making a hidden pre-deadline transfer.

## Privacy and output location

Strategy outputs reveal squad/transfer information. By default they are written
outside the repository at:

`$HOME/.local/share/dastan-smartplay-free/entry-<id>/gw<gw>/`

The runner refuses an output directory inside the Git worktree. Runtime `.vendor`,
`.venv`, legacy `output/` and `solver-output/` paths are ignored by Git as a second
layer of protection.

Each successful run writes `strategy_manifest.json` with SHA-256 bindings for:

- the private source snapshot/team file (hash only);
- Dastan acceptance JSON;
- projection CSV;
- SmartPlay `summary.md`, `picks.csv` and `solution.json`.

## Scientific boundary

Dastan's published live validation is one-gameweek-ahead. This engine therefore
**fails closed for `--horizon` other than 1**. SmartPlay Solver itself supports longer
horizons, but repeating or fabricating future Dastan xPts would not be scientifically
justified.

The early-season hosted SmartPlay comparison remains diagnostic-only for GW1–5 because
its serving overlay is not present in the open Dastan release. That distinction does
not invalidate the open Dastan model or the local solver contract.

## Tests

Pure regression suite:

```bash
python -m unittest -v \
  test_live_gw.py \
  test_live_gw_v2.py \
  test_check_acceptance.py \
  test_strategy.py
```

`test_strategy.py` specifically checks private attestation, exact 15-player state,
FT/bank extraction, duplicate rejection, public-state acknowledgement, private solver
command construction, the certified one-minute GW+1 candidate floor, active-chip and
stale-gameweek rejection, SHA-256 binding and freshness/future-time rejection.

The GitHub acceptance workflow then performs a genuine public GW4 reconstruction and
SmartPlay projection-contract validation. Its one-day artifact contains only public
materials: the projection CSV, acceptance JSON, exact pinned SmartPlay Solver source,
a CPython 3.13-compatible HiGHS wheel, and the exact Official FPL bootstrap/fixtures
used for the offline solve. **No owner state is uploaded by this branch.**

## Cost boundary

The permanent runtime is the user's Mac. Dastan, SmartPlay Solver, Official FPL,
Understat and the mapping source used here are free/public. No paid API, hosted
SmartPlay subscription, second daemon or cloud service is required.

The public-repository GitHub Action exists only as reproducible acceptance evidence;
the operational strategy runner does not depend on Actions.
