from __future__ import annotations

from apex_price_risk.outcomes import label_price_move_within_horizon
from conftest import make_snapshot


def test_move_observed_by_horizon_is_positive() -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    changed = make_snapshot("2026-08-30T21:00:00Z", price=76)
    assert label_price_move_within_horizon(source, 10, [changed]) == (1, 0)


def test_unchanged_post_horizon_observation_confirms_negative() -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    before = make_snapshot("2026-08-30T21:00:00Z", price=75)
    after = make_snapshot("2026-08-31T01:00:00Z", price=75)
    assert label_price_move_within_horizon(source, 10, [before, after]) == (0, 0)


def test_first_change_observed_after_horizon_is_interval_ambiguous() -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    before = make_snapshot("2026-08-30T21:00:00Z", price=75)
    after = make_snapshot("2026-08-31T01:00:00Z", price=76)
    assert label_price_move_within_horizon(source, 10, [before, after]) is None


def test_no_horizon_confirmation_is_not_labelled_negative() -> None:
    source = make_snapshot("2026-08-30T00:00:00Z", price=75)
    before = make_snapshot("2026-08-30T21:00:00Z", price=75)
    assert label_price_move_within_horizon(source, 10, [before]) is None
