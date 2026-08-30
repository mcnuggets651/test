from __future__ import annotations

from typing import Any

from .schemas import ForecastBundle


def build_route_advisory(forecast: ForecastBundle, payload: dict[str, Any]) -> dict[str, Any]:
    if forecast.serving_authorized or forecast.production_influence != "NONE":
        raise RuntimeError("Refusing advisory from a forecast that violates the non-serving contract")
    by_id = {row.element_id: row for row in forecast.rows}
    results: list[dict[str, Any]] = []
    for route in payload.get("routes", []):
        route_id = str(route.get("route_id") or "unnamed")
        targets = [int(value) for value in route.get("targets_that_break_if_rise", [])]
        missing = [element_id for element_id in targets if element_id not in by_id]
        probabilities = [by_id[element_id].p_rise_24h for element_id in targets if element_id in by_id]
        lower = max(probabilities, default=0.0)
        upper = min(1.0, sum(probabilities))
        statuses = {by_id[element_id].model_status for element_id in targets if element_id in by_id}
        qualified = bool(probabilities) and statuses == {"SHADOW_TRAINED_UNQUALIFIED"}
        results.append(
            {
                "route_id": route_id,
                "targets_that_break_if_rise": targets,
                "missing_targets": missing,
                "p_any_breaking_rise_lower_bound": round(lower, 8),
                "p_any_breaking_rise_upper_bound": round(upper, 8),
                "risk_band": _risk_band(lower, upper),
                "model_state": "TRAINED_BUT_UNQUALIFIED" if qualified else "COLD_START_OR_INCOMPLETE",
                "timing_decision_authorized": False,
                "note": "Bounds avoid assuming independence between player price moves.",
            }
        )
    return {
        "schema_version": 1,
        "source_forecast_generated_at_utc": forecast.generated_at_utc,
        "source_model_id": forecast.model_id,
        "advisory_only": True,
        "action_authorized": False,
        "production_influence": "NONE",
        "routes": results,
    }


def _risk_band(lower: float, upper: float) -> str:
    if upper < 0.20:
        return "LOW"
    if lower >= 0.60:
        return "HIGH"
    return "UNCERTAIN"
