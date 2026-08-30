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
- current-GW and season price-change counters;
- availability/status and chance-of-playing information;
- position;
- UTC time-of-day;
- time to next deadline.

No expected-points projection, Apex recommendation, captaincy signal or private manager field is a feature.

## Cold start

Until enough labelled observations exist, V1 emits a transparent heuristic forecast capped at 45% probability and marked `COLD_START`/`LOW` confidence. It exists to validate the end-to-end sealing/evaluation machinery, not to authorize action.

## Trained model

Rise and fall are separate calibrated logistic models.

The dataset is split by observation time, not random rows:

- first 70% of observation timestamps: fit feature scaling and base logistic model;
- next 15%: fit Platt probability calibration;
- final 15%: untouched internal test metrics.

Minimum training requirements per direction:

- 1,200 labelled rows;
- 20 positive outcomes;
- 200 negative outcomes;
- 10 unique observation timestamps;
- both classes present in train, calibration and test periods.

If any requirement fails, that direction remains cold-start.

## Metrics

Internal chronological test metrics:

- Brier score;
- Brier skill versus training-period base-rate forecast;
- log loss;
- average precision;
- 10-bin expected calibration error;
- test prevalence.

Separately, `evaluate` scores the actual timestamped forecasts that were sealed prospectively. That prospective archive, not the training report, is the evidence used by governance.

## Why not a more complex model yet?

Tree ensembles, survival/hazard models and sequence models may eventually improve the signal, but V1 needs a strong interpretable baseline and a clean prospective tournament first. Complexity is only promoted if it demonstrates out-of-sample improvement.
