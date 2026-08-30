# Model

## V1 philosophy

FPL does not publish the complete price-change threshold algorithm. Apex Price Risk therefore treats price movement as a probabilistic forecasting problem rather than pretending an inferred threshold is official truth.

## Features

Features are frozen as `FEATURE_VERSION=1` and include:

- net transfer pressure relative to estimated ownership;
- gross transfer activity relative to estimated ownership;
- short-window net transfer velocity using only the previous snapshot;
- ownership level and ownership velocity;
- current price;
- current-GW and season price-change counters, including fall counters;
- availability/status and chance-of-playing information;
- position;
- UTC time-of-day;
- time to next deadline.

Official ownership is rounded to one decimal place. V1 applies a 0.05 percentage-point denominator floor so displayed `0.0%` ownership cannot create artificial per-owner transfer spikes. Event-transfer velocity is suppressed across Gameweek counter resets or other non-monotonic counter changes.

No expected-points projection, Apex recommendation, captaincy signal or private manager field is a feature.

## Labels

`LABEL_VERSION=2` uses an interval-censoring-safe 24-hour outcome contract. A move first observed only after the 24-hour boundary is not credited to the forecast because its exact side of the boundary is unknowable. See `DATA_CONTRACT.md`.

## Cold start

Until enough labelled observations exist, V1 emits a transparent heuristic forecast capped at 45% probability and marked `COLD_START`/`LOW` confidence. It exists to validate the end-to-end sealing/evaluation machinery, not to authorize action.

## Trained model

Rise and fall are separate calibrated logistic models.

The dataset is divided chronologically using nominal 70% and 85% boundary times, with a **27-hour purge gap** before both calibration and test periods. The purge is longer than the 24-hour target plus three-hour observation grace, preventing labels from an earlier partition from consuming snapshots in the next partition's feature-time region.

- training: observations before the first purge boundary;
- calibration: observations after the first purge and ending before the second purge;
- test: observations after the second purge;
- scaler and base logistic model fit on training only;
- Platt calibration fits on calibration only;
- final metrics use untouched test only.

Minimum training requirements per direction:

- 1,200 labelled rows;
- 20 positive outcomes;
- 200 negative outcomes;
- 32 unique observation timestamps;
- at least 14 days of observation span;
- both classes present in purged train, calibration and test periods.

If any requirement fails, that direction remains cold-start.

## Metrics

Internal purged chronological test metrics:

- Brier score;
- Brier skill versus training-period base-rate forecast;
- log loss;
- average precision;
- 10-bin expected calibration error;
- test prevalence.

Separately, `evaluate` scores the actual timestamped forecasts that were sealed prospectively using the same `LABEL_VERSION`. That prospective archive, not the training report, is the evidence used by governance.

## Why not a more complex model yet?

Tree ensembles, survival/hazard models and sequence models may eventually improve the signal, but V1 needs a strong interpretable baseline and a clean prospective tournament first. Complexity is only promoted if it demonstrates out-of-sample improvement.
