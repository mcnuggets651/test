from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from apex_price_risk.official import capture_from_bootstrap
from apex_price_risk.schemas import PriceSnapshot


def bootstrap_payload(
    *,
    price: int = 75,
    transfers_in: int = 1000,
    transfers_out: int = 200,
    selected_by_percent: str = "10.0",
) -> dict[str, Any]:
    return {
        "total_players": 1_000_000,
        "events": [
            {"id": 2, "is_current": True, "is_next": False, "deadline_time": "2026-08-29T10:00:00Z"},
            {"id": 3, "is_current": False, "is_next": True, "deadline_time": "2026-09-04T17:30:00Z"},
        ],
        "elements": [
            {
                "id": 10,
                "code": 10010,
                "web_name": "Alpha",
                "team": 1,
                "element_type": 3,
                "now_cost": price,
                "selected_by_percent": selected_by_percent,
                "transfers_in_event": transfers_in,
                "transfers_out_event": transfers_out,
                "transfers_in": transfers_in + 100,
                "transfers_out": transfers_out + 100,
                "cost_change_event": 0,
                "cost_change_event_fall": 0,
                "cost_change_start": 0,
                "cost_change_start_fall": 0,
                "status": "a",
                "chance_of_playing_next_round": None,
                "chance_of_playing_this_round": None,
                "can_select": True,
                "can_transact": True,
            }
        ],
    }


def make_snapshot(
    at: str,
    *,
    price: int = 75,
    transfers_in: int = 1000,
    transfers_out: int = 200,
    selected_by_percent: str = "10.0",
) -> PriceSnapshot:
    when = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(UTC)
    return capture_from_bootstrap(
        bootstrap_payload(
            price=price,
            transfers_in=transfers_in,
            transfers_out=transfers_out,
            selected_by_percent=selected_by_percent,
        ),
        when,
    )


@pytest.fixture
def snapshot() -> PriceSnapshot:
    return make_snapshot("2026-08-30T12:00:00Z")
