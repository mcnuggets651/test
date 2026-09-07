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
- adapter, acceptance, strategy, operational-orchestration and AI-context tests green.

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

`authority-aware private owner state -> Official FPL + Understat -> live adapter -> Dastan feature builder/weights -> SmartPlay Solver -> verified strategy manifest -> private AI decision context -> AI interpretation`

The adapter does not retrain Dastan and does not implement a second model. It calls
Dastan's own public reconstruction, feature-building and inference code, with the thin
current-season identity/fixture boundary required to serve the live season.

The AI layer is a read-only sidecar. It **cannot change Dastan xP, expected minutes,
SmartPlay constraints, Solver output, owner state, or account state**.

No SmartPlay website scraping is performed.

## Operational one-command owner solve

The preferred entry point is `operational.py`.

It reuses the existing private `mcnuggets651/fpl` strategy query bridge rather than
reimplementing owner-state recovery. That bridge resolves `latest` through current
public Apex authority, validates immutable private releases/attestations and fails
closed when a current authority-matched manager state is unavailable.

From this directory:

```bash
python3 operational.py --private-repo /path/to/fpl
```

If the private `fpl` checkout is a sibling of this public repository, or
`FPL_PRIVATE_REPO` is set, `--private-repo` can be omitted:

```bash
python3 operational.py
```

Authentication is taken from `GITHUB_TOKEN` when present; otherwise the runner uses
`gh auth token`. The token is never printed or written to an artifact.

For deadline-sensitive decisions, force a new public projection reconstruction:

```bash
python3 operational.py --private-repo ~/fpl --force-refresh
```

Useful policy switches are passed through to the strategy runner:

```bash
python3 operational.py --posture neutral --plan-b
python3 operational.py --no-hits
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
10. generates the private AI sidecar only **after** the core strategy manifest is verified;
11. records AI-sidecar status/hashes in `operational_manifest.json`;
12. treats AI-sidecar failure as non-destructive: the completed Dastan/SmartPlay result remains valid;
13. deletes the temporary private snapshot automatically.

## AI-in-the-loop interpretation

A successful operational run now creates three additional private local files:

- `ai_decision_context.json`
- `ai_decision_brief.md`
- `ai_decision_context.sha256`

All three are written atomically at mode `0600`. They are **never uploaded by public
CI**.

`ai_decision_context.json` contains a structured, hash-verified handoff with:

- exact bank, free transfers and transfer budget;
- the current 15-player squad with exact purchase/selling prices;
- every Dastan GW+1 player xP and expected-minutes forecast;
- per-fixture Dastan evidence including `p60` and `p_any`;
- the exact SmartPlay transfers, XI, captain, vice-captain and bench order;
- Solver total xP, Solver statistics xP and objective score;
- Dastan acceptance/provenance;
- SHA-256 hashes for the private snapshot, model inputs and Solver outputs;
- a machine-readable AI interpretation contract.

The source private snapshot is **not copied wholesale**. Only a decision-relevant
whitelist is retained, so unrelated private fields do not leak into the AI bundle.

The AI contract is deliberately strict:

- Dastan xP/minutes remain fixed model evidence;
- SmartPlay output remains fixed optimizer evidence;
- AI may add separately sourced live context such as injuries, European/cup minutes,
  press conferences, role/set-piece changes and price-change risk;
- AI must not silently replace model values;
- AI must not fabricate future-GW Dastan projections;
- AI must not attach a made-up numerical edge to an unsolved structural alternative;
- the final answer should separate **model result**, **external evidence**,
  **AI interpretation**, and **final recommendation**.

See [`AI_DECISION_CONTRACT.md`](AI_DECISION_CONTRACT.md) for the full trust boundary,
schema and failure-isolation rules.

### Using the model with ChatGPT

After the local run completes, attach:

```text
~/.local/share/dastan-smartplay-free/entry-<id>/gw<gw>/ai_decision_context.json
```

to ChatGPT and ask:

> Interpret this Dastan + SmartPlay decision for the current FPL deadline. Use the model numbers as fixed evidence, research current external context, challenge one-week structural weaknesses, and give one final recommendation. Do not invent unsolved numerical counterfactuals.

`ai_decision_brief.md` contains the same ready-to-use instruction plus the headline
transfer/XI/captain result for quick inspection.

This gives the intended separation:

`Dastan + SmartPlay = quantitative anchor`

`AI = current-context interpretation, challenge and explanation`

## Lower-level strategy runner

`strategy.py` remains available when an exact snapshot/team file has already been
obtained:

```bash
python3 strategy.py --apex-strategy-snapshot /private/path/strategy_snapshot.json
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
python3 strategy.py --team-file /private/path/team.json
```

Public entry mode is a guarded fallback only. Official FPL does not expose the current
FT counter and can hide transfers made since the most recent deadline, so the caller
must explicitly confirm the revealed state and provide FT/bank:

```bash
python3 strategy.py \
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
- `projections/dastan_gw<gw>_fixtures.csv`
- `solution/summary.md`
- `solution/picks.csv`
- `solution/solution.json`
- `strategy_manifest.json`
- `operational_manifest.json`
- `ai_decision_context.json`
- `ai_decision_context.sha256`
- `ai_decision_brief.md`

`strategy_manifest.json` binds the owner-state source hash, Dastan acceptance/projection
hashes and Solver outputs. `operational_manifest.json` additionally binds the private
query-code head/files, immutable manager release, strategy manifest and AI-context
status/hash.

`operational_manifest.json` remains privacy-safe and does not contain the full squad.
The AI context intentionally contains exact manager decision state, so it is classified
`PRIVATE_MANAGER_LOCAL_ONLY` and must not be committed or publicly uploaded.

## Scientific boundary

Dastan's published live validation is one-gameweek-ahead. This engine therefore
**fails closed for horizons other than 1**. SmartPlay Solver supports longer horizons,
but fabricating future Dastan inputs would not be scientifically justified.

The hosted SmartPlay comparison remains diagnostic-only for GW1–5 because its serving
overlay is not present in the open Dastan release. From GW6 the acceptance policy can
require hosted spot-check parity when a current manually captured reference is
available.

The AI layer does not weaken either boundary. It can discuss longer-term squad
structure qualitatively, but it must not label fabricated future Dastan numbers as
model evidence.

## Tests

Pure regression suite:

```bash
python3 -m unittest -v \
  test_live_gw.py \
  test_live_gw_v2.py \
  test_check_acceptance.py \
  test_strategy.py \
  test_operational.py \
  test_ai_context.py
```

`test_strategy.py` covers private attestation, exact 15-player state, FT/bank extraction,
duplicate rejection, public-state acknowledgement, the certified one-minute candidate
floor, active-chip/stale-gameweek rejection, SHA-256 binding and projection freshness.

`test_operational.py` covers private-repo identity, dirty-query rejection, credential
resolution, suppression of private query stdout, snapshot permissions, strategy hash
binding, command policy forwarding, AI-sidecar provenance and failure isolation.

`test_ai_context.py` covers strict private-state whitelisting, exact owner-state
requirements, source/output hash verification, projection probability evidence,
structured transfer/XI/captain/bench extraction, private file permissions, checksums
and the no-invented-counterfactual rule.

The public GitHub acceptance workflow additionally performs a genuine GW4 Dastan
reconstruction and SmartPlay projection-contract validation. Its one-day artifact is
public-only: aggregate/per-fixture projections, acceptance files, exact pinned Solver
source, a CPython 3.13-compatible HiGHS wheel and the exact Official FPL bootstrap/
fixtures used for the offline reproducibility check. **No owner state or AI decision
context is uploaded by this branch.**

## Cost boundary

The permanent runtime is the user's Mac. Dastan, SmartPlay Solver, Official FPL,
Understat and the current mapping source are free/public. Owner state is read from the
existing private GitHub persistence/query plane. The AI context builder is Python
standard-library only.

No paid API, hosted SmartPlay subscription, second daemon or cloud service is required.

The public GitHub Action is reproducible acceptance evidence only; normal operation is
local and does not depend on Actions.
