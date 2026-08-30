# FPL Apex Integration Boundary

## V1: no integration

There is deliberately no runtime arrow from Apex Price Risk into FPL Apex.

The projects may both read public Official FPL facts, but Price Risk does not import, call, check out or mutate the FPL Apex codebase. FPL Apex does not need Price Risk to solve or publish.

## Future read-only contract

If prospective evidence eventually passes governance, the first permitted integration is a frozen advisory JSON artifact that can be displayed beside a transfer plan.

A future consumer must treat:

- missing advisory as `UNAVAILABLE` and continue normally;
- stale advisory as `STALE` and continue normally;
- malformed advisory as `INVALID` and continue normally;
- `action_authorized=false` as non-negotiable.

No future advisory is allowed to change xP.

## Future timing research

Execution timing is a separate decision from transfer selection. A valid study must compare:

- expected financial/route loss from waiting through a predicted price change; versus
- expected information value of waiting for press conferences, injuries, training, European matches and lineups.

Only if that study is prospectively positive should timing influence be considered. Even then, the base Apex transfer must already be independently desirable.
