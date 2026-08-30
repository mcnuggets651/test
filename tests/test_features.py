from __future__ import annotations

from apex_price_risk.features import FEATURE_NAMES, build_feature_rows, build_labeled_examples
from conftest import make_snapshot


def test_features_only_use_current_and_past() -> None:
    previous = make_snapshot("2026-08-30T09:00:00Z", transfers_in=700)
    current = make_snapshot("2026-08-30T12:00:00Z", transfers_in=1000)
    future_a = make_snapshot("2026-08-31T12:00:00Z", price=76, transfers_in=5000)
    future_b = make_snapshot("2026-08-31T12:00:00Z", price=76, transfers_in=9000)
    base = build_feature_rows(current, previous)[0].features
    assert build_feature_rows(current, previous)[0].features == base
    examples_a = build_labeled_examples([previous, current, future_a])
    examples_b = build_labeled_examples([previous, current, future_b])
    current_a = next(example for example in examples_a if example.observed_at_utc == current.captured_at_utc)
    current_b = next(example for example in examples_b if example.observed_at_utc == current.captured_at_utc)
    assert current_a.features == current_b.features
    assert current_a.rise_24h == current_b.rise_24h == 1


def test_event_counter_reset_does_not_create_false_transfer_velocity() -> None:
    previous = make_snapshot(
        "2026-08-30T09:00:00Z", transfers_in=50_000, transfers_out=10_000
    )
    current = make_snapshot(
        "2026-08-30T12:00:00Z", transfers_in=500, transfers_out=100
    )
    row = build_feature_rows(current, previous)[0]
    velocity_index = FEATURE_NAMES.index("net_velocity_per_owner_hour")
    assert row.features[velocity_index] == 0.0


def test_feature_contract_contains_fall_price_counters() -> None:
    assert "cost_change_event_fall" in FEATURE_NAMES
    assert "cost_change_start_fall" in FEATURE_NAMES
