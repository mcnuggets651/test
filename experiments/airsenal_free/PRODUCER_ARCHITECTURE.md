# AIrsenal producer / ChatGPT consumer architecture

This experiment is a **precompute-first model service**, not a conversational compute loop.

The intended product boundary is:

```text
fresh private owner state + Official FPL
        ↓
AIrsenal morning producer on the private Mac
        ↓
forecast → H3 → H5 → combined immutable evidence
        ↓
private `airsenal-results` pointers/manifests
        ↓
ChatGPT reads, verifies, compares and interprets
```

Ordinary ChatGPT questions must never start an AIrsenal solve. Chat is a read-only consumer of already-produced evidence. A model refresh is a separate operational action.

## Staged evidence

A producer run uses one attested owner snapshot and one freshly updated/frozen AIrsenal base database. It publishes stages in this order:

1. **forecast** — five-Gameweek player expected-points table and recent-minutes basis, published before optimizer work;
2. **H3** — independent three-Gameweek optimizer strategy;
3. **H5** — independent five-Gameweek optimizer strategy;
4. **combined** — the existing decision context containing both H3 and H5.

Forecast, H3 and H5 have independent immutable run commits. A slow or failed H5 therefore does not erase already-valid forecast or H3 evidence.

## Private paths

The staged latest manifest is:

```text
airsenal/latest/entry-63984/manifest.json
```

Stage pointers are:

```text
airsenal/latest/entry-63984/forecast.json
airsenal/latest/entry-63984/h3.json
airsenal/latest/entry-63984/h5.json
```

Each pointer references an exact immutable commit under:

```text
airsenal/staged/runs/entry-63984/gw<GW>/<producer-run-id>/<stage>/
```

The legacy combined pointer remains unchanged for backward compatibility:

```text
airsenal/latest/entry-63984.json
```

No existing consumer is required to switch atomically. New ChatGPT retrieval should prefer the staged manifest first and use the legacy pointer when `combined.status == ready`.

## Freshness contract

A stage is usable only when all of the following hold:

- its pointer and immutable artifact verify by SHA-256;
- its target Gameweek is the current Official FPL `is_next` Gameweek;
- its `producer_run_id` matches the current staged manifest;
- its owner-state hash matches the current staged manifest;
- it is no more than 24 hours old unless an explicitly governed narrower policy applies;
- no newer producer run has reset that stage to `pending` or `failed`.

A new producer run immediately replaces the staged manifest with a new owner-state hash and `pending` stages. This prevents ChatGPT from silently using yesterday's strategy after owner state has changed. The older immutable evidence remains available for audit but is no longer the current staged run.

Owner provenance may be at most one published Gameweek behind the current target Gameweek. When that governed one-GW lag is used, current identity, club, position, price and status are rebased against live Official FPL while purchase-price provenance remains immutable. A larger lag fails closed.

## Partial availability semantics

ChatGPT may use only the evidence that is actually ready:

- `forecast=ready`, H3/H5 pending: player xP comparisons are available; optimizer strategy is not.
- `forecast=ready`, `h3=ready`, H5 pending: near-term AIrsenal strategy is available and must be labelled H3-only.
- H5 ready as well: multi-week H5 evidence is available.
- `combined=ready`: the full legacy decision context is also verified and available.

No numerical H5 edge may be invented from H3, and no optimizer recommendation may be invented from forecast-only evidence.

## Producer failure behaviour

A safe failure class is written to the staged manifest for the failed stage. Raw private logs, owner rows and credentials are never copied into the manifest.

A failure in a later stage does not rewrite or delete an earlier immutable successful stage. The overall manifest becomes `degraded` until a newer successful producer run supersedes it.

## Scheduling and manual refresh

The private repository owns scheduling because owner state and model outputs are private. The public experiment contains only the producer implementation and synthetic/public tests.

The scheduled workflow must:

- use only `[self-hosted, macOS, ARM64]`;
- use the exact accepted public experiment SHA;
- run from fresh owner state, not a remembered squad;
- use the governed explicit AIrsenal scenario;
- upload no owner artifact to GitHub Actions;
- write only to the governed `airsenal-results` namespace;
- never call FPL write commands;
- prevent overlapping producer runs with a concurrency group.

Manual refresh is a separate workflow dispatch. Ordinary ChatGPT questions remain read-only.

## ChatGPT retrieval order

For an owner-specific AIrsenal question:

1. fetch `airsenal/latest/entry-63984/manifest.json` from `airsenal-results`;
2. validate Gameweek, age, producer run and available stages;
3. for each needed ready stage, fetch its stage pointer;
4. follow the exact `run_commit_sha` rather than branch head;
5. verify artifact/stage-manifest hashes and owner-state hash;
6. use raw AIrsenal evidence without rewriting its numbers;
7. add current football evidence separately;
8. interpret model disagreement and give the final recommendation.

If the required stage is not ready, say `MODEL EVIDENCE UNAVAILABLE` for that stage rather than substituting Dastan, Apex Next or ChatGPT estimates.

## Compatibility and authority

This architecture does not change model authority:

- Dastan + SmartPlay remain independent evidence;
- AIrsenal remains an independent wrapper;
- `mcnuggets651/fpl` remains owner-state/result transport;
- `mcnuggets651/fpl-apex-next` is not queried or modified by this experiment;
- no AIrsenal result becomes Apex production authority automatically;
- no FPL write path is introduced.
