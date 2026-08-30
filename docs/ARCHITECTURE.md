# Architecture

## Objective

Estimate the probability that an Official FPL player's price rises or falls within the next 24 hours, then quantify whether that movement threatens an already-interesting future transfer route.

This is an execution-risk research system, not an expected-points model.

## System diagram

```text
Official FPL public bootstrap-static
              |
              v
      Point-in-time capture
              |
       deterministic snapshot
              |
      +-------+-------+
      |               |
      v               v
 feature builder   confirmed future
 (past/current)    now_cost changes
      |               |
      v               v
 shadow forecast   mature labels only
      |               |
      +-------+-------+
              |
       chronological train
       + probability calibration
              |
              v
       prospective evaluation
              |
              v
   read-only route-risk diagnostic

              X
              |
              v
        FPL Apex production
```

The `X` is intentional: there is no production connection in V1.

## Code/data separation

`main` is the immutable-ish code surface. Scheduled observations do not commit to it. Public evidence is appended to the `observations` branch under timestamped paths:

```text
snapshots/YYYYMMDDTHHMMSSZ.json.gz
predictions/YYYYMMDDTHHMMSSZ.json
models/YYYYMMDDTHHMMSSZ.json
evaluations/YYYYMMDDTHHMMSSZ.json
```

Past timestamped files are not rewritten by the workflows.

## Failure domains

If Official FPL is unavailable, capture fails and no new evidence is written. If training fails, no new model is written. If the price-risk repository is unavailable, nothing happens to FPL Apex. There is no failover from price risk into Apex because there is no dependency.

## No-hindsight boundary

A forecast records its source snapshot timestamp, Official bootstrap SHA-256, code SHA and model ID. It is written before its outcome is known. Evaluation only scores forecasts once the full horizon has elapsed. Training features use current and previous snapshots only; future snapshots are used solely to create labels.
