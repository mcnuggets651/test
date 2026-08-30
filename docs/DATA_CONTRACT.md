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
- total FPL player count when exposed;
- current/next Gameweek identifiers;
- next deadline;
- one row per Official FPL element ID.

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

## Outcome labels

For a forecast captured at time `t`:

- rise = 1 if a later observed `now_cost` is greater than the price at `t` within the 24-hour horizon plus a maximum three-hour observation grace;
- fall = 1 if a later observed `now_cost` is lower;
- otherwise 0 when the horizon has fully matured;
- rows with no future observation for that element are excluded.

A row is never labelled before the horizon has elapsed.

## Price units

All price arithmetic remains in Official integer tenths. Display conversion to pounds is a UI concern. This prevents floating-point budget errors.
