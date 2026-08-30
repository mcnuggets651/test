# Data Contract

## Canonical machine source

Public Official FPL:

`https://fantasy.premierleague.com/api/bootstrap-static/`

The user-facing `https://fantasy.premierleague.com/en/price-changes` page is a human audit surface, not scraped production input.

## Snapshot identity

Every snapshot contains:

- schema version;
- UTC capture timestamp;
- canonical source URL;
- SHA-256 of the canonicalized raw bootstrap payload;
- total FPL player count;
- current/next Gameweek identifiers;
- next deadline;
- one row per Official FPL element ID.

`total_players` is supplied by the Official bootstrap response and is used only as the public denominator for ownership-normalised transfer features.

## Player fields

V1 retains only fields plausibly relevant to price movement or identity:

- `element_id`, `code`, `web_name`, team and position;
- `now_cost` in Official £0.1m integer units;
- `selected_by_percent`;
- transfers in/out for the current event and season;
- event/season price-change counters;
- status and published chance-of-playing fields;
- `can_select`/`can_transact` when exposed.

No manager authentication, squad, purchase price, selling price, bank, free-transfer state or cookie/token is collected.

## Outcome labels — LABEL_VERSION 2

The target is a price move **within 24 hours of the sealed source snapshot**. Because snapshots observe state rather than the exact instant of a price change, boundary-crossing observations are treated as interval-censored instead of guessed.

For player price `p(t)` at source time `t`:

- rise = 1 if a later snapshot at or before `t + 24h` observes a price above `p(t)`;
- fall = 1 if a later snapshot at or before `t + 24h` observes a price below `p(t)`;
- if no move is observed by `t + 24h`, an unchanged observation from `t + 24h` through `t + 27h` confirms `(rise=0, fall=0)`;
- if the first post-horizon confirming observation has already changed price, the move time is ambiguous across the 24-hour boundary and that row is excluded;
- if no confirming observation exists within the three-hour grace interval, the row is excluded rather than labelled negative;
- rows where the element is absent from the required observations are excluded.

The three-hour grace is an observation-completeness allowance, **not an extension of the prediction target**.

## Price units

All price arithmetic remains in Official integer tenths. Display conversion to pounds is a UI concern. This prevents floating-point budget errors.
