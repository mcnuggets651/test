from __future__ import annotations

from datetime import UTC, datetime

from apex_price_risk.model import ModelBundle, build_forecast, cold_start_bundle
from conftest import make_snapshot


def test_cold_start_roundtrip_is_non_serving() -> None:
    model = cold_start_bundle(datetime(2026, 8, 30, tzinfo=UTC))
    restored = ModelBundle.from_dict(model.to_dict())
    assert restored.rise.kind == "cold_start"
    assert restored.fall.kind == "cold_start"
    assert restored.serving_authorized is False
    assert restored.production_influence == "NONE"


def test_forecast_is_explicitly_advisory_only() -> None:
    previous = make_snapshot("2026-08-30T09:00:00Z", transfers_in=800)
    current = make_snapshot("2026-08-30T12:00:00Z", transfers_in=1100)
    forecast = build_forecast(current, previous, cold_start_bundle(), "deadbeef")
    assert forecast.serving_authorized is False
    assert forecast.production_influence == "NONE"
    assert len(forecast.rows) == 1
    assert 0.0 <= forecast.rows[0].p_rise_24h <= 1.0
    assert forecast.rows[0].model_status == "COLD_START"
