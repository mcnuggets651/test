from __future__ import annotations

from apex_price_risk.advisory import build_route_advisory
from apex_price_risk.model import build_forecast, cold_start_bundle
from conftest import make_snapshot


def test_route_advisory_never_authorizes_action() -> None:
    snapshot = make_snapshot("2026-08-30T12:00:00Z")
    forecast = build_forecast(snapshot, None, cold_start_bundle(), "sha")
    advisory = build_route_advisory(
        forecast,
        {"routes": [{"route_id": "gw4", "targets_that_break_if_rise": [10]}]},
    )
    route = advisory["routes"][0]
    assert advisory["action_authorized"] is False
    assert route["timing_decision_authorized"] is False
    assert route["p_any_breaking_rise_lower_bound"] == route["p_any_breaking_rise_upper_bound"]
