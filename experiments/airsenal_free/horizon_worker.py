#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCHEMA = "airsenal-chat-horizon-result-v1"
SUPPORTED_CHIPS = {"wildcard", "free_hit", "bench_boost", "triple_captain"}
ACTIVE_CHIP_MAP = {"wildcard": "wildcard", "freehit": "free_hit", "free_hit": "free_hit", "bboost": "bench_boost", "bench_boost": "bench_boost", "3xc": "triple_captain", "triple_captain": "triple_captain"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def validate_scenario(raw: dict[str, Any], target_gw: int, active_chip: Any) -> dict[str, Any]:
    allowed = {"max_total_hit", "allow_unused_transfers", "max_opt_transfers", "num_iterations", "num_thread", "chip_gameweeks"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unsupported scenario keys: {unknown}")
    out = {
        "max_total_hit": int(raw.get("max_total_hit", 8)),
        "allow_unused_transfers": bool(raw.get("allow_unused_transfers", False)),
        "max_opt_transfers": int(raw.get("max_opt_transfers", 2)),
        "num_iterations": int(raw.get("num_iterations", 100)),
        "num_thread": int(raw.get("num_thread", 4)),
        "chip_gameweeks": {},
    }
    if out["max_total_hit"] < 0 or out["max_total_hit"] % 4 != 0:
        raise ValueError("max_total_hit must be a non-negative multiple of four")
    if out["max_opt_transfers"] < 0 or out["max_opt_transfers"] > 5:
        raise ValueError("max_opt_transfers must be between 0 and 5")
    if out["num_iterations"] < 1 or out["num_iterations"] > 1000:
        raise ValueError("num_iterations must be 1..1000")
    if out["num_thread"] < 1 or out["num_thread"] > 8:
        raise ValueError("num_thread must be 1..8")
    chip_input = raw.get("chip_gameweeks") or {}
    if not isinstance(chip_input, dict):
        raise ValueError("chip_gameweeks must be an object")
    for key, value in chip_input.items():
        if key not in SUPPORTED_CHIPS:
            raise ValueError(f"unsupported chip {key!r}")
        gw = int(value)
        if gw < 0:
            raise ValueError("chip gameweek must be 0 (search) or a positive GW")
        out["chip_gameweeks"][key] = gw
    if active_chip:
        mapped = ACTIVE_CHIP_MAP.get(str(active_chip).lower())
        if mapped is None:
            raise ValueError(f"unsupported active chip in owner state: {active_chip!r}")
        if out["chip_gameweeks"] and out["chip_gameweeks"].get(mapped) != target_gw:
            raise ValueError("scenario chip constraints conflict with the owner's already-active target-GW chip")
        out["chip_gameweeks"] = {mapped: target_gw}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-state", type=Path, required=True)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--horizon", type=int, choices=(3, 5), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    import airsenal
    from airsenal.framework.player import CandidatePlayer
    from airsenal.framework.multiprocessing_utils import set_multiprocessing_start_method
    from airsenal.framework.prediction_utils import get_recent_minutes_for_player
    from airsenal.framework.schema import session
    from airsenal.framework.season import CURRENT_SEASON
    from airsenal.framework.squad import Squad
    from airsenal.framework.utils import fastcopy, get_player, get_player_from_api_id, list_players
    from airsenal.scripts import fill_transfersuggestion_table as opt
    from airsenal.scripts.fill_predictedscore_table import make_predictedscore_table

    # AIrsenal requires fork on POSIX/macOS for its optimizer workers.
    # Its CLI/pipeline entrypoints call this helper before run_optimization;
    # this direct integration must preserve that upstream initialization.
    set_multiprocessing_start_method()

    owner = load_object(args.owner_state)
    if owner.get("schema") != "airsenal-chat-owner-state-v1":
        raise ValueError("unexpected owner-state schema")
    target_gw = int(owner["target_gameweek"])
    gameweeks = list(range(target_gw, target_gw + args.horizon))
    if gameweeks[-1] > 38:
        raise ValueError(f"AIrsenal cannot produce requested H{args.horizon}: range extends beyond GW38")
    scenario_raw = load_object(args.scenario) if args.scenario else {}
    scenario = validate_scenario(scenario_raw, target_gw, owner.get("active_chip"))

    random.seed(42)
    db_path = Path(__import__("os").environ["AIRSENAL_DB_FILE"]).expanduser().resolve()
    pre_prediction_db_sha256 = sha256_file(db_path)
    prediction_tag = make_predictedscore_table(
        gw_range=gameweeks,
        season=CURRENT_SEASON,
        include_bonus=True,
        include_cards=True,
        include_saves=True,
        include_def_con=True,
        tag_prefix=f"airsenal-chat-h{args.horizon}-",
        dbsession=session,
    )
    post_prediction_db_sha256 = sha256_file(db_path)

    def build_starting_squad() -> Squad:
        squad = Squad(season=CURRENT_SEASON)
        seen: set[int] = set()
        for row in owner["squad"]:
            api_id = int(row["element_id"])
            player = get_player_from_api_id(api_id, dbsession=session)
            if player is None:
                raise ValueError(f"AIrsenal DB cannot map Official FPL element {api_id}")
            if api_id in seen:
                raise ValueError(f"duplicate AIrsenal mapping for element {api_id}")
            seen.add(api_id)
            added = squad.add_player(
                player,
                price=int(row["purchase_price_tenths"]),
                gameweek=target_gw,
                check_budget=False,
                check_team=False,
                dbsession=session,
            )
            if not added:
                raise ValueError(f"failed to add owner element {api_id} to AIrsenal Squad")
        squad.budget = int(owner["bank_tenths"])
        if not squad.is_complete() or len(squad.players) != 15:
            raise ValueError("AIrsenal starting squad is not exact 15")
        return squad

    starting = build_starting_squad()
    price_reconciliation = []
    private_by_api = {int(row["element_id"]): row for row in owner["squad"]}
    for candidate in starting.players:
        db_player = get_player(candidate.player_id, dbsession=session)
        if db_player is None or db_player.fpl_api_id is None:
            raise ValueError(f"AIrsenal player {candidate.player_id} has no Official FPL mapping")
        api_id = int(db_player.fpl_api_id)
        computed = int(starting.get_sell_price_for_player(candidate, use_api=False, gameweek=target_gw, dbsession=session))
        exact = int(private_by_api[api_id]["selling_price_tenths"])
        price_reconciliation.append({"element_id": api_id, "airsenal_selling_price_tenths": computed, "private_selling_price_tenths": exact, "match": computed == exact})
    if not all(row["match"] for row in price_reconciliation):
        mismatches = [row for row in price_reconciliation if not row["match"]]
        raise ValueError(f"PRICE_RECONCILIATION_FAILED: {mismatches}")

    def exact_starting_squad(*_args: Any, **_kwargs: Any) -> Squad:
        return build_starting_squad()

    with patch.object(opt, "get_entry_start_gameweek", return_value=1), patch.object(opt, "get_starting_squad", side_effect=exact_starting_squad):
        _best_squad, best_strategy = opt.run_optimization(
            gameweeks=gameweeks,
            tag=prediction_tag,
            season=CURRENT_SEASON,
            fpl_team_id=int(owner["entry_id"]),
            chip_gameweeks=scenario["chip_gameweeks"],
            num_free_transfers=int(owner["free_transfers"]),
            max_total_hit=scenario["max_total_hit"],
            allow_unused_transfers=scenario["allow_unused_transfers"],
            max_opt_transfers=scenario["max_opt_transfers"],
            num_iterations=scenario["num_iterations"],
            num_thread=scenario["num_thread"],
            profile=False,
            is_replay=False,
        )
    if best_strategy is None:
        raise ValueError("AIrsenal returned no multi-week strategy")

    def decorate_internal(pid: int) -> dict[str, Any]:
        player = get_player(int(pid), dbsession=session)
        if player is None:
            return {"airsenal_player_id": int(pid), "element_id": None, "name": None}
        return {
            "airsenal_player_id": int(pid),
            "element_id": int(player.fpl_api_id) if player.fpl_api_id is not None else None,
            "name": player.name,
            "position": player.position(CURRENT_SEASON),
            "team": player.team(CURRENT_SEASON, target_gw),
        }

    predictions = []
    recent_count = max(args.horizon, 3)
    for player in list_players(season=CURRENT_SEASON, gameweek=target_gw, dbsession=session):
        if player.fpl_api_id is None:
            continue
        candidate = CandidatePlayer(player, CURRENT_SEASON, target_gw, dbsession=session)
        candidate.calc_predicted_points(prediction_tag)
        gw_points = {str(gw): float(candidate.predicted_points.get(prediction_tag, {}).get(gw, 0.0)) for gw in gameweeks}
        try:
            recent_minutes = [int(x) for x in get_recent_minutes_for_player(player, num_match_to_use=recent_count, season=CURRENT_SEASON, last_gw=target_gw - 1, dbsession=session)]
        except Exception:
            recent_minutes = []
        predictions.append({
            "element_id": int(player.fpl_api_id),
            "airsenal_player_id": int(player.player_id),
            "name": player.name,
            "position": player.position(CURRENT_SEASON),
            "team": player.team(CURRENT_SEASON, target_gw),
            "expected_points": gw_points,
            "recent_minutes_basis": recent_minutes,
            "explicit_expected_minutes": None,
        })
    predictions.sort(key=lambda row: row["element_id"])

    route_squad = build_starting_squad()
    team_by_gw: dict[str, Any] = {}
    raw_players_in = best_strategy.get("players_in", {})
    raw_players_out = best_strategy.get("players_out", {})
    raw_chips = best_strategy.get("chips_played", {})
    raw_bank = best_strategy.get("bank", {})
    raw_ft = best_strategy.get("free_transfers", {})
    raw_num = best_strategy.get("num_transfers", {})
    raw_hits = best_strategy.get("points_hit", {})
    raw_points = best_strategy.get("points_per_gw", {})
    raw_discount = best_strategy.get("discount_factor", {})

    for gw in gameweeks:
        outgoing = [int(x) for x in raw_players_out.get(gw, raw_players_out.get(str(gw), []))]
        incoming = [int(x) for x in raw_players_in.get(gw, raw_players_in.get(str(gw), []))]
        chip = raw_chips.get(gw, raw_chips.get(str(gw)))
        working = fastcopy(route_squad) if chip == "free_hit" else route_squad
        for pid in outgoing:
            if not working.remove_player(pid, gameweek=gw, use_api=False, dbsession=session):
                raise ValueError(f"route reconstruction could not remove player {pid} in GW{gw}")
        for pid in incoming:
            if not working.add_player(pid, gameweek=gw, dbsession=session):
                raise ValueError(f"route reconstruction could not add player {pid} in GW{gw}")
        working.optimize_lineup(gw, prediction_tag)
        starters = [p for p in working.players if p.is_starting]
        subs = sorted([p for p in working.players if not p.is_starting], key=lambda p: p.sub_position)
        captain = next((p for p in working.players if p.is_captain), None)
        vice = next((p for p in working.players if p.is_vice_captain), None)
        discount = float(raw_discount.get(gw, raw_discount.get(str(gw), 1.0)))
        net_discounted = float(raw_points.get(gw, raw_points.get(str(gw), 0.0)))
        hit = int(raw_hits.get(gw, raw_hits.get(str(gw), 0)) or 0)
        team_by_gw[str(gw)] = {
            "gameweek": gw,
            "transfers_out": [decorate_internal(pid) for pid in outgoing],
            "transfers_in": [decorate_internal(pid) for pid in incoming],
            "chip": chip,
            "bank_tenths": int(raw_bank.get(gw, raw_bank.get(str(gw), working.budget))),
            "free_transfers_after": int(raw_ft.get(gw, raw_ft.get(str(gw), 0)) or 0),
            "num_transfers": raw_num.get(gw, raw_num.get(str(gw))),
            "points_hit": hit,
            "discount_factor": discount,
            "net_expected_points_after_hit": (net_discounted / discount) if discount else None,
            "squad_expected_points_before_hit": ((net_discounted / discount) + hit) if discount else None,
            "squad": [decorate_internal(p.player_id) for p in working.players],
            "starting_xi": [decorate_internal(p.player_id) for p in starters],
            "captain": decorate_internal(captain.player_id) if captain else None,
            "vice_captain": decorate_internal(vice.player_id) if vice else None,
            "bench": [decorate_internal(p.player_id) for p in subs],
        }
        if chip != "free_hit":
            route_squad = working

    best_strategy_json = jsonable(best_strategy)
    decorated_strategy = {
        "immediate_transfers_out": team_by_gw[str(target_gw)]["transfers_out"],
        "immediate_transfers_in": team_by_gw[str(target_gw)]["transfers_in"],
        "future_transfers": {str(gw): {"out": team_by_gw[str(gw)]["transfers_out"], "in": team_by_gw[str(gw)]["transfers_in"]} for gw in gameweeks[1:]},
        "expected_points_by_gameweek": {str(gw): team_by_gw[str(gw)]["squad_expected_points_before_hit"] for gw in gameweeks},
        "net_expected_points_by_gameweek": {str(gw): team_by_gw[str(gw)]["net_expected_points_after_hit"] for gw in gameweeks},
        "cumulative_expected_points_before_hits": sum(float(team_by_gw[str(gw)]["squad_expected_points_before_hit"] or 0.0) for gw in gameweeks),
        "cumulative_net_expected_points": sum(float(team_by_gw[str(gw)]["net_expected_points_after_hit"] or 0.0) for gw in gameweeks),
    }

    result = {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "horizon": args.horizon,
        "gameweeks": gameweeks,
        "entry_id": int(owner["entry_id"]),
        "target_gameweek": target_gw,
        "airsenal_version_reported": getattr(airsenal, "__version__", None),
        "airsenal_season": CURRENT_SEASON,
        "db": {"path_redacted": True, "pre_prediction_sha256": pre_prediction_db_sha256, "post_prediction_sha256": post_prediction_db_sha256},
        "prediction": {
            "tag": prediction_tag,
            "team_model": "ExtendedDixonColesMatchPredictor",
            "player_model": "ConjugatePlayerModel",
            "include_bonus": True,
            "include_cards": True,
            "include_saves": True,
            "include_defensive_contributions": True,
            "recent_minutes_sample_size": recent_count,
            "explicit_expected_minutes_exposed": False,
            "players": predictions,
        },
        "optimizer": {
            "settings": scenario,
            "raw_strategy": best_strategy_json,
            "raw_objective_total_score": float(best_strategy["total_score"]),
            "root_gameweek": int(best_strategy.get("root_gw", target_gw)),
            "strategy": decorated_strategy,
            "team_by_gameweek": team_by_gw,
            "starting_price_reconciliation": price_reconciliation,
        },
        "limitations": {
            "objective_discounting": "upstream default exponential (14/15)^n",
            "future_plan_commitment": "planning evidence only; upstream assumes later re-optimisation",
            "named_player_keep_force_constraints": "not exposed by this integration",
            "future_price_forecast": "not modelled",
            "minutes": "recent-minutes sample averaged inside point calculation; no scalar xMin field",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
