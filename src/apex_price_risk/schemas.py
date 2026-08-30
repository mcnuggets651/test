from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = 1
FEATURE_VERSION = 1
LABEL_VERSION = 2


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    element_id: int
    code: int
    web_name: str
    team: int
    element_type: int
    now_cost: int
    selected_by_percent: float
    transfers_in_event: int
    transfers_out_event: int
    transfers_in: int
    transfers_out: int
    cost_change_event: int
    cost_change_event_fall: int
    cost_change_start: int
    cost_change_start_fall: int
    status: str
    chance_of_playing_next_round: int | None
    chance_of_playing_this_round: int | None
    can_select: bool | None
    can_transact: bool | None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> PlayerSnapshot:
        return cls(**row)


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    schema_version: int
    captured_at_utc: str
    source_url: str
    bootstrap_sha256: str
    total_players: int
    current_event_id: int | None
    next_event_id: int | None
    next_deadline_utc: str | None
    players: tuple[PlayerSnapshot, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["players"] = [asdict(player) for player in self.players]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceSnapshot:
        return cls(
            schema_version=int(payload["schema_version"]),
            captured_at_utc=str(payload["captured_at_utc"]),
            source_url=str(payload["source_url"]),
            bootstrap_sha256=str(payload["bootstrap_sha256"]),
            total_players=int(payload.get("total_players", 0)),
            current_event_id=_optional_int(payload.get("current_event_id")),
            next_event_id=_optional_int(payload.get("next_event_id")),
            next_deadline_utc=_optional_str(payload.get("next_deadline_utc")),
            players=tuple(PlayerSnapshot.from_dict(row) for row in payload["players"]),
        )

    def player_map(self) -> dict[int, PlayerSnapshot]:
        return {player.element_id: player for player in self.players}


@dataclass(frozen=True, slots=True)
class FeatureRow:
    observed_at_utc: str
    element_id: int
    now_cost: int
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ForecastRow:
    element_id: int
    web_name: str
    now_cost: int
    p_rise_24h: float
    p_fall_24h: float
    confidence: str
    model_status: str


@dataclass(frozen=True, slots=True)
class ForecastBundle:
    schema_version: int
    feature_version: int
    generated_at_utc: str
    source_snapshot_at_utc: str
    source_bootstrap_sha256: str
    code_sha: str
    model_id: str
    horizon_hours: int
    serving_authorized: bool
    production_influence: str
    rows: tuple[ForecastRow, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rows"] = [asdict(row) for row in self.rows]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ForecastBundle:
        return cls(
            schema_version=int(payload["schema_version"]),
            feature_version=int(payload["feature_version"]),
            generated_at_utc=str(payload["generated_at_utc"]),
            source_snapshot_at_utc=str(payload["source_snapshot_at_utc"]),
            source_bootstrap_sha256=str(payload["source_bootstrap_sha256"]),
            code_sha=str(payload["code_sha"]),
            model_id=str(payload["model_id"]),
            horizon_hours=int(payload["horizon_hours"]),
            serving_authorized=bool(payload["serving_authorized"]),
            production_influence=str(payload["production_influence"]),
            rows=tuple(ForecastRow(**row) for row in payload["rows"]),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
