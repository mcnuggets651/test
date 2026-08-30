# Operations

## Scheduled collection

`Price Risk Capture` runs at minute 17 every three hours. GitHub cron can be delayed; the timestamp inside each snapshot is the actual UTC acquisition time and is authoritative.

Sequence:

1. check out `main`;
2. install the current package;
3. run the architecture boundary check;
4. load the existing `observations` branch if present;
5. fetch fresh Official bootstrap data;
6. create a deterministic gzip snapshot;
7. generate a forecast using the latest historical model artifact, or cold start if none exists;
8. append timestamped snapshot + forecast to `observations`;
9. push only after both files exist.

## Daily training/evaluation

`Price Risk Train and Evaluate` runs daily at 08:23 UTC and shares a non-cancelling concurrency group with capture.

It:

- reads all accumulated snapshots;
- creates only mature 24-hour labels;
- trains chronological models;
- scores sealed historical forecasts;
- appends timestamped model/evaluation artifacts.

## Failure semantics

This service is optional by design.

- Capture failure: no new snapshot/forecast; FPL Apex unaffected.
- Training failure: previous shadow model remains historical evidence; FPL Apex unaffected.
- Missing model: capture falls back to cold-start forecast; still non-serving.
- Missing `observations` branch on first capture: workflow creates it.
- No mature forecasts: evaluation returns `INSUFFICIENT`, not a fabricated metric.

## Manual use

`workflow_dispatch` is enabled for capture and evaluation. Manual runs are for operational recovery or deliberate research, not for repeatedly searching for a more favourable probability.

## Retention

Evidence is stored in Git history on the `observations` branch using compressed snapshot files. This avoids expiring Actions artifacts and preserves the exact point-in-time forecast archive required for prospective evaluation.
