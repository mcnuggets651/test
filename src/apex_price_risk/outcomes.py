from __future__ import annotations

from datetime import datetime, timedelta

from .schemas import PriceSnapshot


def label_price_move_within_horizon(
    source: PriceSnapshot,
    element_id: int,
    later_snapshots: list[PriceSnapshot],
    *,
    horizon_hours: int = 24,
    grace_hours: int = 3,
) -> tuple[int, int] | None:
    """Return (rise, fall) without crediting boundary-ambiguous moves.

    A price move observed at or before the target horizon is a positive label.
    If no move is observed by the target, an unchanged observation at/after the
    target (within the grace interval) confirms a negative label. If the first
    confirming observation after the target has already changed, the exact move
    time is interval-censored across the target boundary and the row is excluded.
    """
    if horizon_hours <= 0 or grace_hours < 0:
        raise ValueError("horizon_hours must be positive and grace_hours non-negative")

    current = source.player_map().get(element_id)
    if current is None:
        return None

    source_time = _dt(source.captured_at_utc)
    target_time = source_time + timedelta(hours=horizon_hours)
    grace_end = target_time + timedelta(hours=grace_hours)
    ordered = sorted(later_snapshots, key=lambda snapshot: snapshot.captured_at_utc)

    observed_by_target = []
    confirmations = []
    for snapshot in ordered:
        observed_at = _dt(snapshot.captured_at_utc)
        if observed_at <= source_time or observed_at > grace_end:
            continue
        player = snapshot.player_map().get(element_id)
        if player is None:
            continue
        if observed_at <= target_time:
            observed_by_target.append(player)
        if observed_at >= target_time:
            confirmations.append(player)

    rose = any(player.now_cost > current.now_cost for player in observed_by_target)
    fell = any(player.now_cost < current.now_cost for player in observed_by_target)
    if rose or fell:
        return int(rose), int(fell)

    if not confirmations:
        return None

    first_confirmation = confirmations[0]
    if first_confirmation.now_cost != current.now_cost:
        # The move happened somewhere between the final pre-target observation
        # and this post-target observation. Its side of the 24h boundary is unknown.
        return None

    return 0, 0


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
