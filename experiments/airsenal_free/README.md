# FPL AIrsenal — isolated multi-week decision lab

This directory is an **isolated, non-serving, read-only decision experiment**. It runs the official upstream AIrsenal code on the owner's Mac against an immutable owner snapshot obtained through the existing private `PRIVATE_MANAGER` query boundary. It does not modify Apex, Apex Next, Dastan, `test:main`, or the FPL account.

## Fixed boundaries

- Public experiment: `mcnuggets651/test:prototype/airsenal-chat`.
- Code root: `experiments/airsenal_free/`.
- Upstream: `alan-turing-institute/AIrsenal` pinned to `453e4797e85232854004a753deda6bcdc81062b5`.
- Upstream license: MIT; see `THIRD_PARTY_NOTICES.md`.
- Private input: `mcnuggets651/fpl` using the same `tools/apex_strategy_query.py --run-id latest` mechanism used by the Dastan experiment.
- Private output: generated branch `airsenal-results`, never `dastan-results`.
- Entry: `63984`.
- Horizons: independent 3-GW and 5-GW runs from the same owner snapshot and the same pre-prediction AIrsenal database snapshot.
- Runtime data: outside all Git worktrees under `${AIRSENAL_CHAT_HOME:-~/.local/share/airsenal-chat}`.
- No FPL write commands are invoked. `airsenal_make_transfers` and `airsenal_set_lineup` are explicitly outside this experiment.

## One-command use

After checking out this branch and after the governed private `airsenal-results` transport has been accepted:

```bash
cd ~/test
./experiments/airsenal_free/run.sh --private-repo ~/fpl
```

The command bootstraps the pinned upstream runtime if needed, validates the private repository/query boundary, obtains a fresh immutable `PRIVATE_MANAGER` snapshot, validates it against live Official FPL `bootstrap-static` and `fixtures`, updates an isolated AIrsenal database, freezes that database, runs independent 3-GW and 5-GW prediction/optimisation workers, writes a private decision bundle locally, and attempts the two-commit private Git publication protocol.

To keep a scientifically valid result local:

```bash
./experiments/airsenal_free/run.sh --private-repo ~/fpl --no-chat-publish
```

No password, refresh token, FPL cookie, or raw auth payload is read or persisted by this experiment. GitHub authentication is taken from `GITHUB_TOKEN` or `gh auth token`, matching the Dastan bridge pattern.

## What is validated before optimisation

The owner adapter fails closed unless all of the following hold:

- `run.attestation_scope == PRIVATE_MANAGER`;
- private release is immutable;
- entry ID is exactly `63984`;
- private target Gameweek equals the live Official FPL event marked `is_next`;
- `team_state.published_gw` equals that target Gameweek;
- `state_complete_for_transfers == true`;
- exactly 15 unique players with 2 GK, 5 DEF, 5 MID and 3 FWD;
- at most three players per club;
- bank is a non-negative integer and free transfers are in the FPL range 1–5;
- every owned player has purchase and selling prices;
- Official FPL identity, club, position, current price and status still agree with the private snapshot;
- the isolated AIrsenal database maps every FPL element ID exactly once;
- AIrsenal's own sale-price calculation for the starting squad matches every exact private selling price.

The raw private query file is temporary. Only an explicit allowlist is copied to `owner_state.json`; extra/private/secret-looking fields are discarded rather than recursively copied.

## Why the 3-GW and 5-GW workers use separate database copies

AIrsenal's pinned prediction function uses the requested Gameweek range when choosing its recent-minutes history window. A 3-GW prediction therefore is not necessarily identical to the first three rows of a 5-GW prediction. To preserve upstream behaviour while holding the underlying data constant, the operational runner:

1. updates one isolated base SQLite database;
2. hashes that database;
3. snapshots it to a run directory;
4. creates one copy for H3 and one for H5;
5. runs AIrsenal prediction and optimisation independently in each copy.

Both horizons therefore share the exact same owner state and pre-prediction data snapshot without fabricating future xP.

## Upstream behaviour preserved

The worker calls upstream `make_predictedscore_table()` and upstream `run_optimization()` rather than replacing their model or optimiser. The only optimiser integration seam is a temporary in-process substitution of `get_starting_squad()` and `get_entry_start_gameweek()` so AIrsenal receives the already-attested private owner squad instead of logging into FPL. Exact private free transfers are passed through `num_free_transfers`.

Default experiment settings deliberately expose rather than hide upstream choices:

- extended Dixon-Coles team model;
- conjugate player model;
- bonus, cards, goalkeeper saves and defensive-contribution components enabled;
- future Gameweeks discounted by AIrsenal's default exponential factor `(14/15)^n` in the optimisation objective;
- up to two transfer moves per Gameweek in the normal optimiser tree;
- transfer hits cost 4 points per transfer beyond available free transfers;
- maximum saved free transfers is five;
- no chip is assumed available merely because the owner may still hold it.

If `active_chip` says a supported chip is already active for the target Gameweek, the worker can represent that target-GW chip. It does not infer the owner's unused chip inventory because the narrow owner query does not expose a complete chip inventory.

## Scientific limitations of pinned upstream AIrsenal

These limitations describe the pinned upstream implementation, not a ChatGPT judgement layer.

### Minutes / appearance

AIrsenal does not persist a separate probabilistic expected-minutes scalar for each future Gameweek. In the pinned prediction path it takes a recent-minutes sample, computes appearance/attacking/defending and empirical auxiliary points for each sampled minutes value, then averages the resulting point totals. The number of recent fixtures used is linked to the requested prediction range with a minimum of three. The decision bundle therefore exposes the recent-minutes basis where available and marks explicit scalar xMin as unavailable rather than inventing one.

### Injuries and absences

Predictions are set to zero when recent minutes sum to zero, when AIrsenal's player record says injured/suspended over the target interval, or when a recorded historical absence applies. Official FPL status is separately validated at run time. AIrsenal does not automatically ingest all late press-conference nuance, training reports, European/cup minutes, tactical-role changes or manager quotes.

### Fixture / scoring model

The default team model is extended Dixon-Coles. Player goal/assist involvement is estimated from historical player/team data. The pinned point calculation also includes appearance, clean-sheet/goals-conceded, empirically fitted bonus, yellow/red-card expectation, goalkeeper save expectation and defensive-contribution expectation. It uses FPL data available to its database; it is not a comprehensive event-level xG/xA tactical model and should not be described as one.

### New players

New or low-history players depend on AIrsenal's database/player-model fallback and empirical priors. Sparse history can materially reduce the reliability of their individual projection. The experiment does not override that with ChatGPT scouting numbers.

### Prices and transfers

AIrsenal uses current database prices for candidates and its own FPL sale-price calculation for owned players. The experiment requires that calculation to reconcile to the exact private selling prices at the starting Gameweek. AIrsenal does not predict future price rises/falls as part of football expected points. Future transfer plans are planning evidence, not commitments: upstream itself assumes re-optimisation later.

### Captaincy / bench

AIrsenal optimises the legal formation, orders substitutes by predicted points and selects captain/vice-captain from the two highest projected players in the squad. The experiment exports those choices as upstream evidence; it does not silently substitute a ChatGPT captain.

### Chips

Upstream supports explicit wildcard, free hit, bench boost and triple-captain scheduling/search. This experiment does not guess chip availability from missing data. An already-active supported target-GW chip may be represented; other chip scenarios require an explicit supported scenario input.

### Future-transfer search

The upstream optimiser recursively searches transfer-count strategies and, at each Gameweek, finds the best transfer(s) for the remaining horizon. Normal exact 0/1/2-transfer paths use deterministic search; paths above two transfers and wildcard/free-hit squad construction involve heuristic/random search controlled by upstream. Raw optimiser objective fields and iteration settings are retained in the context.

## Output bundle

A successful local run lives under:

```text
~/.local/share/airsenal-chat/runs/entry-63984/gw<GW>/<run-id>/
```

It contains at least:

- `decision_context.json` — complete private AI decision context;
- `decision_context.sha256` — digest of that context;
- `owner_state.json` — allowlisted transfer-safe owner state only;
- `official_fpl_provenance.json` — endpoint hashes, target GW and retrieval time;
- `h3/result.json` and `h5/result.json` — raw/structured horizon evidence;
- `run_manifest.json` — local computation and transport status.

For each horizon the result retains prediction tag, pre/post DB hashes, player projections by Gameweek, recent-minutes basis when exposed, raw strategy (`total_score`, `points_per_gw`, `free_transfers`, `num_transfers`, `points_hit`, `discount_factor`, `players_in`, `players_out`, `chips_played`, `bank`), decorated transfer names/IDs, and reconstructed per-GW squad/XI/captain/vice/bench when reconstruction is faithful.

The decision context has four explicit interpretation layers:

1. `airsenal_result` — immutable model/optimiser evidence;
2. `external_evidence` — initially empty; ChatGPT may later add separately sourced and timestamped football evidence;
3. `ai_interpretation` — initially empty; never rewrites AIrsenal values;
4. `final_recommendation` — initially unset; produced conversationally, not during the quantitative solve.

## Scenario support

`--scenario <json-file>` accepts only constraints that map directly to pinned upstream optimiser controls:

```json
{
  "max_total_hit": 4,
  "allow_unused_transfers": true,
  "max_opt_transfers": 2,
  "chip_gameweeks": {"triple_captain": 4}
}
```

Named-player constraints such as “keep Bruno”, “force Rogers”, “buy Isak” or “do not sell Gvardiol” are **not claimed as supported** by this wrapper because the pinned public optimiser API does not expose a stable named-player keep/force contract. The correct behaviour is to report that limitation, not fake a counterfactual xP edge. A future implementation may add such scenarios only if mapped to a genuine upstream-supported constraint seam and tested separately.

## Private ChatGPT bridge

Governed private publication targets:

```text
mcnuggets651/fpl @ airsenal-results
airsenal/runs/entry-63984/gw<GW>/<run-id>/...
airsenal/latest/entry-63984.json
```

Publication is two commits:

1. append the unique immutable run directory and push fast-forward;
2. update the latest pointer to the exact immutable run commit and push fast-forward.

The publisher refuses an existing immutable run path, never force-pushes, uses a temporary detached worktree, verifies the remote head after each push, and compares the owner's normal private working-tree status before/after. A publication failure is recorded separately and does not invalidate a completed quantitative solve.

A fresh connected ChatGPT session should read `AIRSENAL_CHAT_BRIDGE.md` in the private repository, fetch `airsenal/latest/entry-63984.json` from `airsenal-results`, then fetch the exact context path at the pointer's immutable `run_commit_sha` and validate its SHA/integrity before interpretation.

The owner-facing phrase is:

> **Check my latest AIrsenal strategy.**

## Failure classes

- `OWNER_STATE_INVALID` — private state is incomplete, mutable, stale or mismatched.
- `OFFICIAL_FPL_INVALID` — live Official FPL cannot establish one current next Gameweek or factual reconciliation fails.
- `UPSTREAM_PIN_INVALID` — runtime is not the exact pinned AIrsenal commit/license/lock identity.
- `DATA_BOOTSTRAP_FAILED` — isolated AIrsenal database could not be set up/updated.
- `PRICE_RECONCILIATION_FAILED` — AIrsenal's current sale-price mechanics disagree with exact owner state.
- `HORIZON_FAILED` — AIrsenal cannot produce a valid 3-GW or 5-GW prediction/optimisation surface; no fabricated fallback.
- `CONTEXT_FAILED` — computation completed but decision-context integrity could not be established.
- `CHAT_PUBLISH_FAILED` — local computation/context remain valid; private transport failed.

## Reproduction

Every context records the owner release/run provenance, raw snapshot SHA-256, official endpoint hashes, pinned upstream SHA, upstream lock identity, public experiment commit SHA, base DB hash, horizon DB hashes, prediction tags, CLI/API settings and file hashes. Reproduction means checking out the same experiment commit and upstream pin, resolving the same immutable owner release if still retrievable, and using the recorded Official/DB evidence. A new live run is expected to differ when Official FPL data or owner state has changed.

## Public CI and privacy

`.github/workflows/airsenal-chat-free.yml` runs only synthetic/public tests. It deliberately injects fake secret-looking fields and asserts they are excluded. The upstream smoke job clones the exact SHA and imports the real pinned AIrsenal stack. A public `macos-14` job verifies the same bootstrap/import path on GitHub's standard public M1/arm64 runner; standard runners for public repositories are free. No owner snapshot, private repository checkout or genuine decision context is uploaded to Actions.
