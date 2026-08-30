from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from .outcomes import label_price_move_within_horizon
from .schemas import FeatureRow, PlayerSnapshot, PriceSnapshot

FEATURE_NAMES: tuple[str, ...] = (
    "net_event_per_owner",
    "gross_event_per_owner",
    "net_velocity_per_owner_hour",
    "selected_pct",
    "selected_velocity_pct_hour",
    "now_cost_scaled",
    "cost_change_event",
    "cost_change_event_fall",
    "cost_change_start",
    "cost_change_start_fall",
    "available",
    "chance_next_scaled",
    "chance_missing",
    "position_gk",
    "position_def",
    "position_mid",
    "position_fwd",
    "hour_sin",
    "hour_cos",
    "hours_to_deadline_scaled",
)

# Official selected_by_percent is rounded to one decimal place. A displayed 0.0%
# therefore does not mean one owner. Use half a display quantum as a conservative
# denominator floor so low-owned players cannot create artificial velocity explosions.
OWNERSHIP_FLOOR_PERCENT = 0.05


@dataclass(frozen=True, slots=True)
class LabeledExample:
    observed_at_utc: str
    element_id: int
    features: tuple[float, ...]
    rise_24h: int
    fall_24h: int


def build_feature_rows(
    current: PriceSnapshot, previous: PriceSnapshot | None
) -> list[FeatureRow]:
    previous_map = {} if previous is None else previous.player_map()
    current_time = _dt(current.captured_at_utc)
    previous_time = None if previous is None else _dt(previous.captured_at_utc)
    elapsed_hours = 0.0
    if previous_time is not None:
        elapsed_hours = max((current_time - previous_time).total_seconds() / 3600.0, 0.0)

    rows: list[FeatureRow] = []
    for player in current.players:
        prev = previous_map.get(player.element_id)
        features = _features_for_player(current, player, prev, elapsed_hours, previous)
        rows.append(
            FeatureRow(
                observed_at_utc=current.captured_at_utc,
                element_id=player.element_id,
                now_cost=player.now_cost,
                features=features,
            )
        )
    return rows


def build_labeled_examples(
    snapshots: list[PriceSnapshot], horizon_hours: int = 24, grace_hours: int = 3
) -> list[LabeledExample]:
    if horizon_hours <= 0 or grace_hours < 0:
        raise ValueError("horizon_hours must be positive and grace_hours non-negative")
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.captured_at_utc)
    examples: list[LabeledExample] = []
    for index, current in enumerate(ordered):
        previous = ordered[index - 1] if index > 0 else None
        later = ordered[index + 1 :]
        for row in build_feature_rows(current, previous):
            outcome = label_price_move_within_horizon(
                current,
                row.element_id,
                later,
                horizon_hours=horizon_hours,
                grace_hours=grace_hours,
            )
            if outcome is None:
                continue
            rise, fall = outcome
            examples.append(
                LabeledExample(
                    observed_at_utc=row.observed_at_utc,
                    element_id=row.element_id,
                    features=row.features,
                    rise_24h=rise,
                    fall_24h=fall,
                )
            )
    return examples


def _features_for_player(
    snapshot: PriceSnapshot,
    player: PlayerSnapshot,
    previous: PlayerSnapshot | None,
    elapsed_hours: float,
    previous_snapshot: PriceSnapshot | None,
) -> tuple[float, ...]:
    owners = _estimated_owners(snapshot.total_players, player.selected_by_percent)
    net_event = player.transfers_in_event - player.transfers_out_event
    gross_event = player.transfers_in_event + player.transfers_out_event
    net_velocity = 0.0
    selected_velocity = 0.0
    if previous is not None and 0.25 <= elapsed_hours <= 12.0:
        previous_total = snapshot.total_players if previous_snapshot is None else previous_snapshot.total_players
        previous_owners = _estimated_owners(previous_total, previous.selected_by_percent)
        counters_monotonic = (
            player.transfers_in_event >= previous.transfers_in_event
            and player.transfers_out_event >= previous.transfers_out_event
        )
        same_event = (
            previous_snapshot is not None
            and snapshot.current_event_id is not None
            and snapshot.current_event_id == previous_snapshot.current_event_id
        )
        if counters_monotonic and same_event:
            delta_net = (player.transfers_in_event - previous.transfers_in_event) - (
                player.transfers_out_event - previous.transfers_out_event
            )
            net_velocity = delta_net / max(previous_owners, owners) / elapsed_hours
        selected_velocity = (player.selected_by_percent - previous.selected_by_percent) / elapsed_hours

    chance_missing = float(player.chance_of_playing_next_round is None)
    if player.chance_of_playing_next_round is None:
        chance_next = 1.0 if player.status == "a" else 0.5
    else:
        chance_next = player.chance_of_playing_next_round / 100.0

    now = _dt(snapshot.captured_at_utc)
    radians = 2.0 * math.pi * (now.hour + now.minute / 60.0) / 24.0
    hours_to_deadline = 168.0
    if snapshot.next_deadline_utc:
        hours_to_deadline = max(
            0.0, min(168.0, (_dt(snapshot.next_deadline_utc) - now).total_seconds() / 3600.0)
        )

    return (
        net_event / owners,
        gross_event / owners,
        net_velocity,
        player.selected_by_percent / 100.0,
        selected_velocity / 100.0,
        player.now_cost / 100.0,
        float(player.cost_change_event),
        float(player.cost_change_event_fall),
        float(player.cost_change_start),
        float(player.cost_change_start_fall),
        float(player.status == "a"),
        chance_next,
        chance_missing,
        float(player.element_type == 1),
        float(player.element_type == 2),
        float(player.element_type == 3),
        float(player.element_type == 4),
        math.sin(radians),
        math.cos(radians),
        hours_to_deadline / 168.0,
    )


def _estimated_owners(total_players: int, selected_by_percent: float) -> float:
    effective_percent = max(selected_by_percent, OWNERSHIP_FLOOR_PERCENT)
    return max(total_players * effective_percent / 100.0, 1.0)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
