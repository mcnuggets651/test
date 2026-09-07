# Dastan + SmartPlay Solver — £0 local strategy engine

This directory is an isolated experiment on branch `prototype/dastan-smartplay`.
It does **not** modify Apex Price Risk on `main`, FPL Apex, or FPL Apex Next.

## Status

The GW+1 engine is operational and reproducible from free/public inputs.

The acceptance workflow has demonstrated a complete live GW4 run with:

- 654 Official-FPL player rows accepted by SmartPlay Solver;
- Dastan public artifact contract verified;
- current Coventry/Hull Understat mappings verified;
- exact released Dastan weights used for inference;
- SmartPlay Solver projection validation passed;
- adapter, acceptance, strategy and operational-orchestration tests green.

The hosted SmartPlay service applies a separate early-season serving overlay during
GW1–5. Hosted values are therefore **diagnostic only** in that period. This engine is
open Dastan + open SmartPlay Solver; it is not described as exact hosted SmartPlay
parity during GW1–5.

## Frozen upstream inputs

The public model/solver stack is consumed at exact commits:

- Dastan: `19376523afdec4836d0e6b5632c6773d0fe40c53`
- SmartPlay Solver: `7ec56e944982020f8709db5d00b0b78821fb1f38`
- SmartPlay public mappings: `9b5bec6ae12541be24decd980e119af90617a868`

`bootstrap_v2.sh` verifies those pins before the strategy runner may continue.

## Architecture

`authority-aware private owner state -> Official FPL + Understat -> live adapter ->
Dastan feature builder/weights -> SmartPlay Solver -> one recommendation`

The adapter does not retrain Dastan and does not implement a second model. It calls
Dastan's own public reconstruction, feature-building and inference code, with the thin
current-season identity/fixture boundary required to serve the live season.

No SmartPlay website scraping is performed.

## Operational one-command owner solve

The preferred entry point is `operational.py`.

It reuses the existing private `mcnuggets651/fpl` strategy query bridge rather than
reimplementing owner-state recovery. That bridge resolves `latest` through current
public Apex authority, validates immutable private releases/attestations and fails
closed when a current authority-matched manager state is unavailable.

From this directory:

```bash
python operational.py --private-repo /path/to/fpl
```

If the private `fpl` checkout is a sibling of this public repository, or
`FPL_PRIVATE_REPO` is set, `--private-repo` can be omitted:

```bash
python operational.py
```

Authentication is taken from `GITHUB_TOKEN` when present; otherwise the runner uses
`gh auth token`. The token is never printed or written to an artifact.

For deadline-sensitive decisions, force a new public projection reconstruction:

```bash
python operational.py --force-refresh
```

Useful policy switches are passed through to the strategy runner:

```bash
python operational.py --posture neutral --plan-b
python operational.py --no-hits
```

Normal operation leaves hits available to the Solver; it will only use them when its
objective prefers them. `--no-hits` is an explicit counterfactual restriction, not the
default policy.

### Operational safety contract

`operational.py`:

1. requires the local private checkout's `origin` to be exactly `mcnuggets651/fpl`;
2. refuses to execute locally modified private query-bridge files;
3. obtains an authority-aware `latest` immutable `PRIVATE_MANAGER` snapshot;
4. suppresses the private query tool's stdout so the owner squad is not echoed;
5. stores the temporary snapshot in a `0700` temporary directory and the file at `0600`;
6. invokes `strategy.py` with that exact snapshot;
7. validates that `strategy_manifest.json` is SHA-256-bound to the queried snapshot;
8. refuses output inside either the public or private Git worktree;
9. uses an exclusive per-entry lock to prevent concurrent solves from racing the same state;
10. writes a privacy-safe `operational_manifest.json` containing query-code SHA/provenance,
    manager release identity and hashes, but not the 15-player squad;
11. deletes the temporary private snapshot automatically.

## Lower-level strategy runner

`strategy.py` remains available when an exact snapshot/team file has already been
obtained:

```bash
python strategy.py --apex-strategy-snapshot /private/path/strategy_snapshot.json
```

The private Apex snapshot must be:

- `PRIVATE_MANAGER` attested;
- immutable;
- transfer-complete;
- exactly 15 unique players;
- complete for purchase prices, selling prices, bank and free transfers;
- targeted at the exact current Official-FPL `is_next` gameweek;
- free of an active chip (this no-chip GW+1 runner fails closed rather than ignoring one).

A native SmartPlay team JSON is also accepted:

```bash
python strategy.py --team-file /private/path/team.json
```

Public entry mode is a guarded fallback only. Official FPL does not expose the current
FT counter and can hide transfers made since the most recent deadline, so the caller
must explicitly confirm the revealed state and provide FT/bank:

```bash
python strategy.py \
  --entry-id 63984 \
  --public-state-is-current \
  --free-transfers 2 \
  --bank 0.5
```

Do not use public entry mode after making a hidden pre-deadline transfer.

## Solver candidate floor

For the certified one-GW solve the runner explicitly passes
`--min-expected-minutes 1`.

The pinned Solver internally treats a zero value as its legacy default of 100 minutes,
which would wrongly suppress most GW+1 transfer candidates. A one-minute floor keeps
the full viable player pool while excluding literal zero-minute candidates;
SmartPlay's existing sub-30-minute EV scaling remains active. Regression tests lock
this behavior.

## Privacy and outputs

Personal strategy outputs are written outside the repository by default:

`$HOME/.local/share/dastan-smartplay-free/entry-<id>/gw<gw>/`

A successful operational run contains:

- `projections/dastan_gw<gw>_acceptance.json`
- `projections/dastan_gw<gw>_solver.csv`
- `solution/summary.md`
- `solution/picks.csv`
- `solution/solution.json`
- `strategy_manifest.json`
- `operational_manifest.json`

`strategy_manifest.json` binds the owner-state source hash, Dastan acceptance/projection
hashes and Solver outputs. `operational_manifest.json` additionally binds the private
query-code head/files and immutable manager release used to obtain that owner state.
Neither manifest contains the full private squad.

## Scientific boundary

Dastan's published live validation is one-gameweek-ahead. This engine therefore
**fails closed for horizons other than 1**. SmartPlay Solver supports longer horizons,
but fabricating future Dastan inputs would not be scientifically justified.

The hosted SmartPlay comparison remains diagnostic-only for GW1–5 because its serving
overlay is not present in the open Dastan release. From GW6 the acceptance policy can
require hosted spot-check parity when a current manually captured reference is
available.

## Tests

Pure regression suite:

```bash
python -m unittest -v \
  test_live_gw.py \
  test_live_gw_v2.py \
  test_check_acceptance.py \
  test_strategy.py \
  test_operational.py
```

`test_strategy.py` covers private attestation, exact 15-player state, FT/bank extraction,
duplicate rejection, public-state acknowledgement, the certified one-minute candidate
floor, active-chip/stale-gameweek rejection, SHA-256 binding and projection freshness.

`test_operational.py` covers private-repo identity, dirty-query rejection, credential
resolution, suppression of private query stdout, snapshot permissions, strategy hash
binding, command policy forwarding, and a full fake-owner orchestration from private
query through the final operational manifest.

The public GitHub acceptance workflow additionally performs a genuine GW4 Dastan
reconstruction and SmartPlay projection-contract validation. Its one-day artifact is
public-only: projection/acceptance files, exact pinned Solver source, a CPython
3.13-compatible HiGHS wheel and the exact Official FPL bootstrap/fixtures used for the
offline reproducibility check. **No owner state is uploaded by this branch.**

## Cost boundary

The permanent runtime is the user's Mac. Dastan, SmartPlay Solver, Official FPL,
Understat and the current mapping source are free/public. Owner state is read from the
existing private GitHub persistence/query plane. No paid API, hosted SmartPlay
subscription, second daemon or cloud service is required.

The public GitHub Action is reproducible acceptance evidence only; normal operation is
local and does not depend on Actions.
