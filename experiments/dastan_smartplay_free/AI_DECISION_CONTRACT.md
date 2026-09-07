# AI decision context contract

## Purpose

`ai_context.py` is a **read-only post-solve sidecar** for the isolated Dastan + SmartPlay experiment.
It turns a completed, verified private owner solve into a single structured bundle that an AI analyst can consume without scraping terminal text or inventing model values.

It does not change Dastan, SmartPlay Solver, FPL state, transfer state, or any repository authority.

The intended flow is:

`authority-aware private owner state -> Official FPL + Understat -> Dastan -> SmartPlay Solver -> verified strategy manifest -> AI decision context -> AI interpretation`

## Trust boundary

The quantitative layers are authoritative for what they actually compute:

- Dastan owns GW+1 projected points and expected minutes.
- SmartPlay Solver owns the optimized legal transfer/XI/captain/bench result and objective statistics.
- The immutable private manager snapshot owns exact squad, purchase/selling prices, bank and free transfers.
- The AI layer owns **interpretation only**.

The AI layer must never silently rewrite Dastan xP, expected minutes, Solver output, owner state, or the certified one-gameweek horizon.

## Generated private files

A successful operational run writes these additional files beside the existing strategy outputs:

- `ai_decision_context.json` — complete machine-readable decision bundle;
- `ai_decision_brief.md` — compact human-readable handoff and ready-to-use ChatGPT instruction;
- `ai_decision_context.sha256` — checksum for the JSON bundle.

All three are written atomically with mode `0600` and are classified `PRIVATE_MANAGER_LOCAL_ONLY`.
They are never added to the public GitHub Actions artifact.

## Context schema

Current schema: `dastan-smartplay-ai-context-v1`.

The bundle contains:

### `scope`

- entry ID;
- target gameweek;
- certified projection horizon;
- model/optimizer identity;
- interpretation purpose.

### `owner_state`

A strict whitelist of decision-relevant private state:

- entry ID;
- published/target gameweek;
- bank;
- free transfers;
- active-chip state;
- squad sell value;
- exact transfer budget.

The source snapshot is **not copied wholesale**. Unrelated/private fields are deliberately omitted.

### `current_squad`

For each of the 15 current players:

- FPL element ID;
- name/team/position when available;
- purchase price;
- selling price;
- current price when present in the private snapshot;
- Dastan GW+1 xP;
- Dastan expected minutes;
- per-fixture probability evidence when available.

### `model_evidence`

- every validated GW+1 player projection;
- top 30 by Dastan xP for quick review;
- per-fixture xP/minutes plus `p60` and `p_any` from the public Dastan export;
- acceptance metadata and early-season hosted-parity diagnostic status.

The optimizer continues to consume the aggregate solver CSV; the AI bundle preserves both aggregate evidence and per-fixture context.

### `optimizer_evidence`

Derived directly from SmartPlay Solver's structured `solution.json`:

- transfers in/out;
- transfer count and penalized-transfer count;
- starting XI;
- captain and vice-captain;
- bench order;
- post-solve bank and FT state;
- Solver total xP;
- Solver statistics xP;
- Solver objective score;
- Solver metadata and exact generated summary.

No markdown parsing is used to infer these fields.

### `integrity`

The context verifies and records SHA-256 for:

- private strategy snapshot;
- strategy manifest;
- Dastan acceptance;
- Dastan aggregate solver CSV;
- Dastan per-fixture CSV;
- SmartPlay `solution.json`;
- SmartPlay `picks.csv`;
- SmartPlay `summary.md`.

Before generation, the sidecar fails closed if the strategy manifest does not hash-bind to the supplied private snapshot or if any strategy-manifest source/output hash no longer matches the file on disk.

### `ai_contract`

The bundle carries its own interpretation rules so a fresh AI session receives the scientific boundary with the data.

## AI rules

An AI analyst consuming the bundle must:

1. state the raw model/optimizer result before editorial interpretation;
2. treat Dastan xP/minutes as immutable model evidence;
3. treat SmartPlay output as immutable optimizer evidence;
4. never invent a numerical advantage for an unsolved counterfactual;
5. never fabricate future-GW Dastan projections;
6. separately source and timestamp external evidence such as injuries, European/cup minutes, press conferences, roles, set pieces and price-change signals;
7. explain conflicts between live evidence and model assumptions rather than modifying model numbers;
8. clearly separate **model result**, **external evidence**, **AI interpretation**, and **final recommendation**;
9. identify reversal conditions when new information could change the final recommendation;
10. never execute FPL transfers or account actions from the interpretation bundle.

## What the AI is specifically useful for

The sidecar is designed to let AI challenge issues a one-week optimizer may not fully capture, for example:

- premium-budget concentration;
- whether a downgrade unlocks a structurally better second asset;
- European or domestic-cup congestion;
- late injury/minutes information;
- role/set-piece changes;
- bench fragility;
- captaincy opportunity cost;
- difficulty of buying back a sold premium;
- price-change timing;
- whether the raw GW+1 edge is too small to justify using a transfer.

Those are interpretations around the fixed quantitative anchor, not replacements for it.

## Counterfactual discipline

A route that has not been run through a deterministic scenario solve can be described as a **structural hypothesis**, but the AI must not attach a fabricated xP edge to it.

Example:

- Allowed: `Bruno -> Rogers may unlock Isak and improve squad structure; this should be checked as a deterministic scenario.`
- Not allowed: `Bruno -> Rogers + Isak is +3.2 xP` unless that exact scenario has actually been solved and the number is present in evidence.

## Failure isolation

The AI context is downstream of the core model result.

If context generation fails:

- Dastan projections remain untouched;
- SmartPlay Solver output remains untouched;
- `strategy_manifest.json` remains the core integrity authority;
- `operational_manifest.json` records `ai_decision_context.status = failed`;
- `core_model_result_valid = true` is recorded for the sidecar failure;
- the operational command still preserves the valid recommendation.

This prevents an interpretation-layer regression from turning into a model outage.

## Public/private separation

Public CI tests the sidecar with synthetic owner state only.
The public acceptance artifact may include public Dastan projection/fixture evidence, but **never** `ai_decision_context.json`, `ai_decision_brief.md`, private snapshots, private squad state, bank, free transfers, or sell prices.

## ChatGPT usage

After a successful local operational run, attach:

`~/.local/share/dastan-smartplay-free/entry-<id>/gw<gw>/ai_decision_context.json`

and ask:

> Interpret this Dastan + SmartPlay decision for the current FPL deadline. Use the model numbers as fixed evidence, research current external context, challenge one-week structural weaknesses, and give one final recommendation. Do not invent unsolved numerical counterfactuals.

`ai_decision_brief.md` contains the same ready-to-use instruction plus the headline decision for convenient inspection.

## Tests

`test_ai_context.py` covers:

- immutable `PRIVATE_MANAGER` enforcement;
- exact 15-player transfer-complete owner state;
- strict whitelisting so unrelated private snapshot fields do not leak;
- manifest/source/output SHA-256 enforcement;
- projection and `p60`/`p_any` preservation;
- structured transfer/XI/captain/bench extraction;
- `0600` private output permissions;
- checksum generation;
- explicit prohibition on invented counterfactual numbers.

`test_operational.py` additionally proves that:

- the one-command flow records a ready AI context when generation succeeds;
- an AI-sidecar failure cannot invalidate or erase the completed core model result.
