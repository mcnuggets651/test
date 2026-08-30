from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apex_price_risk.official import FPL_BOOTSTRAP_URL, capture_from_bootstrap
from conftest import bootstrap_payload


def test_capture_uses_official_identity_and_price_units() -> None:
    snapshot = capture_from_bootstrap(
        bootstrap_payload(price=76), datetime(2026, 8, 30, 12, tzinfo=UTC)
    )
    assert snapshot.source_url == FPL_BOOTSTRAP_URL
    assert snapshot.players[0].element_id == 10
    assert snapshot.players[0].now_cost == 76
    assert snapshot.next_event_id == 3
    assert len(snapshot.bootstrap_sha256) == 64


def test_duplicate_element_ids_fail_closed() -> None:
    payload = bootstrap_payload()
    payload["elements"].append(dict(payload["elements"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        capture_from_bootstrap(payload)
