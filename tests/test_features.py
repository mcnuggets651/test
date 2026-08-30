from __future__ import annotations

from apex_price_risk.features import build_feature_rows, build_labeled_examples
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
