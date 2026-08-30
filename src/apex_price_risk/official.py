from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from .schemas import PlayerSnapshot, PriceSnapshot, SCHEMA_VERSION

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_PRICE_CHANGES_PAGE = "https://fantasy.premierleague.com/en/price-changes"
USER_AGENT = "ApexPriceRisk/0.1 (+point-in-time public FPL research)"


def fetch_bootstrap(
    timeout_seconds: float = 20.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    request = Request(FPL_BOOTSTRAP_URL, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("Official FPL bootstrap payload is missing elements")
    if not isinstance(payload.get("events"), list):
        raise ValueError("Official FPL bootstrap payload is missing events")
    return payload


def capture_from_bootstrap(
    payload: dict[str, Any], captured_at: datetime | None = None
) -> PriceSnapshot:
    captured_at = (captured_at or datetime.now(UTC)).astimezone(UTC)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bootstrap_sha = hashlib.sha256(raw).hexdigest()
    events = payload.get("events", [])
    current_event = next((event for event in events if event.get("is_current")), None)
    next_event = next((event for event in events if event.get("is_next")), None)

    players = tuple(_player_from_element(row) for row in payload["elements"])
    if len({player.element_id for player in players}) != len(players):
        raise ValueError("Official FPL bootstrap contains duplicate element ids")

    return PriceSnapshot(
        schema_version=SCHEMA_VERSION,
        captured_at_utc=captured_at.isoformat(),
        source_url=FPL_BOOTSTRAP_URL,
        bootstrap_sha256=bootstrap_sha,
        total_players=int(payload.get("total_players") or 0),
        current_event_id=_event_id(current_event),
        next_event_id=_event_id(next_event),
        next_deadline_utc=None if next_event is None else next_event.get("deadline_time"),
        players=players,
    )


def _event_id(event: dict[str, Any] | None) -> int | None:
    return None if event is None else int(event["id"])


def _player_from_element(row: dict[str, Any]) -> PlayerSnapshot:
    return PlayerSnapshot(
        element_id=int(row["id"]),
        code=int(row.get("code") or 0),
        web_name=str(row.get("web_name") or ""),
        team=int(row.get("team") or 0),
        element_type=int(row.get("element_type") or 0),
        now_cost=int(row["now_cost"]),
        selected_by_percent=_float(row.get("selected_by_percent")),
        transfers_in_event=int(row.get("transfers_in_event") or 0),
        transfers_out_event=int(row.get("transfers_out_event") or 0),
        transfers_in=int(row.get("transfers_in") or 0),
        transfers_out=int(row.get("transfers_out") or 0),
        cost_change_event=int(row.get("cost_change_event") or 0),
        cost_change_event_fall=int(row.get("cost_change_event_fall") or 0),
        cost_change_start=int(row.get("cost_change_start") or 0),
        cost_change_start_fall=int(row.get("cost_change_start_fall") or 0),
        status=str(row.get("status") or ""),
        chance_of_playing_next_round=_optional_int(row.get("chance_of_playing_next_round")),
        chance_of_playing_this_round=_optional_int(row.get("chance_of_playing_this_round")),
        can_select=_optional_bool(row.get("can_select")),
        can_transact=_optional_bool(row.get("can_transact")),
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)
