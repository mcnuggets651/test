from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss

from .outcomes import label_price_move_within_horizon
from .schemas import LABEL_VERSION, ForecastBundle, PriceSnapshot


def evaluate_forecast_history(
    snapshots: list[PriceSnapshot], prediction_paths: list[Path]
) -> dict[str, Any]:
    if not snapshots:
        return {
            "status": "INSUFFICIENT",
            "reason": "NO_SNAPSHOTS",
            "paired_rows": 0,
            "label_version": LABEL_VERSION,
        }
    ordered = sorted(snapshots, key=lambda item: item.captured_at_utc)
    snapshot_by_time = {item.captured_at_utc: item for item in ordered}
    rise_y: list[int] = []
    rise_p: list[float] = []
    fall_y: list[int] = []
    fall_p: list[float] = []
    evaluated_forecasts = 0

    for path in sorted(prediction_paths):
        with path.open("r", encoding="utf-8") as handle:
            forecast = ForecastBundle.from_dict(json.load(handle))
        source = snapshot_by_time.get(forecast.source_snapshot_at_utc)
        if source is None:
            continue
        later = [snapshot for snapshot in ordered if snapshot.captured_at_utc > source.captured_at_utc]
        paired_this_forecast = 0
        for row in forecast.rows:
            outcome = label_price_move_within_horizon(
                source,
                row.element_id,
                later,
                horizon_hours=forecast.horizon_hours,
                grace_hours=3,
            )
            if outcome is None:
                continue
            rise, fall = outcome
            rise_y.append(rise)
            fall_y.append(fall)
            rise_p.append(row.p_rise_24h)
            fall_p.append(row.p_fall_24h)
            paired_this_forecast += 1
        if paired_this_forecast:
            evaluated_forecasts += 1

    if not rise_y:
        return {
            "status": "INSUFFICIENT",
            "reason": "NO_MATURE_UNAMBIGUOUS_FORECASTS",
            "paired_rows": 0,
            "label_version": LABEL_VERSION,
        }
    return {
        "status": "EVALUATED",
        "label_version": LABEL_VERSION,
        "evaluated_forecasts": evaluated_forecasts,
        "paired_rows": len(rise_y),
        "rise": _metrics(rise_y, rise_p),
        "fall": _metrics(fall_y, fall_p),
        "integration_authorized": False,
    }


def _metrics(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    y = np.asarray(labels, dtype=int)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1.0 - 1e-9)
    result: dict[str, Any] = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "prevalence": float(y.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "calibration_bins": _calibration_bins(y, p),
    }
    result["average_precision"] = (
        float(average_precision_score(y, p)) if len(set(int(v) for v in y.tolist())) > 1 else None
    )
    return result


def _calibration_bins(
    labels: np.ndarray[Any, np.dtype[np.int_]], probabilities: np.ndarray[Any, np.dtype[np.float64]]
) -> list[dict[str, float | int]]:
    bins: list[dict[str, float | int]] = []
    edges = np.linspace(0.0, 1.0, 11)
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        mask = (probabilities >= low) & (probabilities < high if high < 1.0 else probabilities <= high)
        if not mask.any():
            continue
        bins.append(
            {
                "low": float(low),
                "high": float(high),
                "n": int(mask.sum()),
                "mean_probability": float(probabilities[mask].mean()),
                "observed_rate": float(labels[mask].mean()),
            }
        )
    return bins
