from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from apex_price_risk.evaluate import evaluate_forecast_history
from apex_price_risk.model import build_forecast, cold_start_bundle
from apex_price_risk.schemas import LABEL_VERSION
from conftest import make_snapshot


def test_only_mature_sealed_forecasts_are_scored(tmp_path: Path) -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    future = make_snapshot("2026-08-31T00:00:00Z", price=76)
    forecast = build_forecast(
        source,
        None,
        cold_start_bundle(datetime(2026, 8, 30, tzinfo=UTC)),
        "abc123",
        datetime(2026, 8, 30, tzinfo=UTC),
    )
    path = tmp_path / "forecast.json"
    path.write_text(json.dumps(forecast.to_dict()), encoding="utf-8")
    immature = evaluate_forecast_history([source], [path])
    mature = evaluate_forecast_history([source, future], [path])
    assert immature["status"] == "INSUFFICIENT"
    assert immature["label_version"] == LABEL_VERSION
    assert mature["status"] == "EVALUATED"
    assert mature["rise"]["positives"] == 1
    assert mature["integration_authorized"] is False
    assert mature["label_version"] == LABEL_VERSION


def test_boundary_ambiguous_move_is_not_scored(tmp_path: Path) -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    before = make_snapshot("2026-08-30T21:00:00Z", price=75)
    after = make_snapshot("2026-08-31T01:00:00Z", price=76)
    forecast = build_forecast(source, None, cold_start_bundle(), "abc123")
    path = tmp_path / "forecast.json"
    path.write_text(json.dumps(forecast.to_dict()), encoding="utf-8")
    report = evaluate_forecast_history([source, before, after], [path])
    assert report["status"] == "INSUFFICIENT"
    assert report["reason"] == "NO_MATURE_UNAMBIGUOUS_FORECASTS"
