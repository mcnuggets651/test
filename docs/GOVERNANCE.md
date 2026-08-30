# Governance

## Current authority

Apex Price Risk V1 is **shadow only**.

- serving authority: false;
- FPL Apex production influence: none;
- transfer authorization: false;
- early-transfer authorization: false;
- xP adjustment authority: never.

No workflow may auto-promote those permissions.

## Evidence ladder

1. **Capture** — point-in-time Official evidence and sealed forecasts accumulate.
2. **Shadow trained** — models may train once minimum data requirements are met.
3. **Prospective evaluation** — sealed predictions are scored only after their horizon matures.
4. **Read-only advisory review** — only after evidence gates pass may a future integration proposal expose price risk beside an Apex plan.
5. **Execution-timing research** — a separate prospective study must show that moving earlier because of price risk creates more value than the information value lost by acting before team/injury news.
6. **Timing influence** — requires an explicit architecture/governance decision; never automatic from model metrics.

## Minimum evidence before read-only integration can even be proposed

All are required:

- at least 21 calendar days of prospective collection;
- at least 5,000 mature paired forecast/player observations;
- at least 75 observed rises and 75 observed falls;
- positive Brier skill versus a pre-event empirical base-rate benchmark for both directions;
- average precision above prevalence for both directions;
- 10-bin calibration error no worse than 0.05 overall;
- no material identity/data-integrity defect;
- no unexplained systematic calibration failure in a populated high-probability bucket;
- stable performance across at least three consecutive weekly windows.

Passing these gates does **not** authorize action. It authorizes a human architecture review for read-only diagnostics.

## Route-risk semantics

For multiple target players, V1 does not multiply independent probabilities. It reports conservative probability bounds for at least one breaking rise:

- lower bound = maximum individual rise probability;
- upper bound = minimum of 1 and the sum of individual rise probabilities.

This avoids fabricating correlation knowledge.

## Permanent rules

Price risk may never:

- change canonical player xP;
- masquerade as football ability;
- become a required source for Apex production;
- cause Apex to fail if unavailable;
- trigger an early transfer solely to bank £0.1m;
- use future information in a historical forecast;
- silently promote itself after a good short run.
