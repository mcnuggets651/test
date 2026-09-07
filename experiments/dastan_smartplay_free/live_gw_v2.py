#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

import live_gw as support

PARITY_MAE_MAX = 0.25
PARITY_MAX_ABS_MAX = 0.50
PARITY_MIN_ROUNDED_MATCHES = 4


def load_current_mappings(public_repo: Path, feature_module) -> tuple[dict[int, int], dict[str, str]]:
    mapping_dir = public_repo / "data" / "mappings"
    players = pd.read_csv(mapping_dir / "players_golden_record.csv", low_memory=False)
    clubs = pd.read_csv(mapping_dir / "clubs_golden_record.csv", low_memory=False)

    club_map = {
        str(row.club_name): str(row.understat_name)
        for row in clubs.dropna(subset=["club_name", "understat_name"]).itertuples()
    }
    # Extend Dastan's in-memory normalizer without modifying upstream source bytes.
    feature_module.FPL_TO_UNDERSTAT.update(club_map)

    usable = players.dropna(subset=["fpl_code", "understat_player_id"]).copy()
    code_to_understat = {
        int(row.fpl_code): int(row.understat_player_id) for row in usable.itertuples()
    }
    return code_to_understat, club_map


def build_current_canonical(
    raw_dir: Path,
    season: str,
    code_to_understat: dict[int, int],
    fpl_to_understat,
    load_understat_player_matches,
    merge_understat_players,
) -> pd.DataFrame:
    core = raw_dir / "vaastav" / season
    gameweeks = pd.read_csv(core / "merged_gw.csv", low_memory=False)
    players = pd.read_csv(core / "players_raw.csv", low_memory=False)[["id", "code"]].rename(
        columns={"id": "element", "code": "fpl_code"}
    )
    teams = pd.read_csv(core / "teams.csv", low_memory=False)
    team_names = teams.set_index("id")["name"].to_dict()

    gameweeks = gameweeks[gameweeks["position"].isin({"GK", "GKP", "DEF", "MID", "FWD"})].copy()
    gameweeks["position"] = gameweeks["position"].replace({"GK": "GKP"})
    gameweeks["gameweek"] = pd.to_numeric(
        gameweeks["GW"] if "GW" in gameweeks else gameweeks["round"], errors="raise"
    ).astype(int)
    gameweeks["season"] = season
    gameweeks = gameweeks.merge(players, on="element", how="left", validate="many_to_one")
    if gameweeks["fpl_code"].isna().any():
        raise RuntimeError("current FPL rows contain players without stable fpl_code")
    gameweeks["fpl_code"] = gameweeks["fpl_code"].astype(int)
    gameweeks = gameweeks.rename(
        columns={"name": "player_name", "team": "team_name", "was_home": "is_home"}
    )
    gameweeks["opponent_team_name"] = gameweeks["opponent_team"].map(team_names)
    gameweeks["us_opponent"] = gameweeks["opponent_team_name"].map(fpl_to_understat)
    gameweeks["kickoff_time"] = pd.to_datetime(
        gameweeks["kickoff_time"], utc=True, errors="coerce"
    )
    if gameweeks["kickoff_time"].isna().any():
        raise RuntimeError("current FPL rows contain invalid kickoff_time")
    gameweeks["match_date"] = gameweeks["kickoff_time"].dt.date.astype(str)
    gameweeks["understat_id"] = gameweeks["fpl_code"].map(code_to_understat)
    gameweeks["expected_points_pre_deadline"] = 0.0
    for column in (
        "starts",
        "clearances_blocks_interceptions",
        "defensive_contribution",
        "recoveries",
        "tackles",
    ):
        if column not in gameweeks:
            gameweeks[column] = pd.NA
    gameweeks = gameweeks.drop_duplicates(["season", "fixture", "fpl_code"], keep="last")

    understat = load_understat_player_matches(raw_dir, [season])
    return merge_understat_players(gameweeks, understat)


def build_export_lookup(bootstrap: dict[str, Any]) -> pd.DataFrame:
    """Official FPL identity metadata, keyed by stable fpl_code.

    Dastan's feature builder intentionally discards display-only columns such as
    player_name. Reattach them after prediction from Official FPL rather than relying
    on incidental feature-frame columns.
    """
    team_names = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    positions = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    rows: list[dict[str, Any]] = []
    for player in bootstrap["elements"]:
        position = positions.get(int(player["element_type"]))
        if position is None:
            raise RuntimeError(f"unknown FPL element_type for element {player['id']}")
        team_id = int(player["team"])
        if team_id not in team_names:
            raise RuntimeError(f"unknown FPL team id {team_id} for element {player['id']}")
        rows.append(
            {
                "fpl_code": int(player["code"]),
                "element": int(player["id"]),
                "player_name": str(
                    player.get("web_name") or player.get("second_name") or player["id"]
                ),
                "team_name": team_names[team_id],
                "position": position,
            }
        )
    lookup = pd.DataFrame(rows)
    duplicates = lookup[lookup.duplicated("fpl_code", keep=False)]
    if not duplicates.empty:
        raise RuntimeError(
            "Official FPL returned duplicate stable fpl_code values: "
            f"{duplicates['fpl_code'].astype(int).tolist()[:10]}"
        )
    return lookup


def attach_export_metadata(predicted: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """Attach canonical display/identity metadata and fail on identity conflicts."""
    if "fpl_code" not in predicted.columns:
        raise RuntimeError("prediction frame has no fpl_code")
    if lookup["fpl_code"].duplicated().any():
        raise RuntimeError("export lookup contains duplicate fpl_code values")

    metadata = ["element", "player_name", "team_name", "position"]
    official = lookup[["fpl_code", *metadata]].rename(
        columns={column: f"official_{column}" for column in metadata}
    )
    out = predicted.merge(official, on="fpl_code", how="left", validate="many_to_one")

    missing_lookup = out["official_element"].isna()
    if missing_lookup.any():
        sample = out.loc[missing_lookup, "fpl_code"].astype(int).tolist()[:10]
        raise RuntimeError(f"Official FPL export metadata missing for fpl_code values: {sample}")

    # Where the model frame preserved canonical identity, verify that it agrees with
    # Official FPL. player_name is deliberately excluded because it is display-only
    # and Dastan may omit or format it differently.
    if "element" in predicted.columns:
        left = pd.to_numeric(out["element"], errors="coerce")
        right = pd.to_numeric(out["official_element"], errors="coerce")
        conflicts = left.notna() & right.notna() & left.ne(right)
        if conflicts.any():
            raise RuntimeError("prediction element identity conflicts with Official FPL")
    for column in ("team_name", "position"):
        if column in predicted.columns:
            left = out[column].astype("string")
            right = out[f"official_{column}"].astype("string")
            conflicts = left.notna() & right.notna() & left.ne(right)
            if conflicts.any():
                sample = out.loc[conflicts, ["fpl_code", column, f"official_{column}"]].head()
                raise RuntimeError(
                    f"prediction {column} conflicts with Official FPL:\n{sample.to_string(index=False)}"
                )

    for column in metadata:
        out[column] = out[f"official_{column}"]
        out = out.drop(columns=[f"official_{column}"])

    if out[metadata].isna().any().any():
        raise RuntimeError("canonical export metadata contains null values")
    return out


def evaluate_reference(comparison: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared parity gate for the five manually observed SmartPlay values."""
    thresholds = {
        "mae_max": PARITY_MAE_MAX,
        "max_abs_delta_max": PARITY_MAX_ABS_MAX,
        "min_rounded_1dp_matches": PARITY_MIN_ROUNDED_MATCHES,
    }
    if not comparison.get("available"):
        return {"status": "NOT_CHECKED", "passed": False, "thresholds": thresholds}

    rows = comparison.get("rows", [])
    matched_rows = [row for row in rows if row.get("xpts") is not None]
    deltas = [abs(float(row["delta"])) for row in matched_rows if row.get("delta") is not None]
    rounded_matches = sum(
        round(float(row["xpts"]), 1) == round(float(row["smartplay_xpts"]), 1)
        for row in matched_rows
    )
    total = int(comparison.get("total", len(rows)))
    matched = int(comparison.get("matched", len(matched_rows)))
    mae = comparison.get("mae")
    max_abs_delta = max(deltas) if deltas else None

    passed = bool(
        total > 0
        and matched == total
        and mae is not None
        and float(mae) <= PARITY_MAE_MAX
        and max_abs_delta is not None
        and max_abs_delta <= PARITY_MAX_ABS_MAX
        and rounded_matches >= min(PARITY_MIN_ROUNDED_MATCHES, total)
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "matched": matched,
        "total": total,
        "mae": mae,
        "max_abs_delta": max_abs_delta,
        "rounded_1dp_matches": rounded_matches,
        "thresholds": thresholds,
    }


def export_predictions(
    predicted: pd.DataFrame,
    gameweek: int,
    output_dir: Path,
    reference: Path | None,
    season: str,
    event: dict[str, Any],
    current_mapping_count: int,
) -> None:
    required_export = [
        "element",
        "fpl_code",
        "player_name",
        "team_name",
        "position",
        "gameweek",
        "fixture",
        "kickoff_time",
        "xpts",
        "expected_minutes",
        "p60",
        "p_any",
    ]
    missing_export = [column for column in required_export if column not in predicted.columns]
    if missing_export:
        raise RuntimeError(f"prediction frame misses export columns: {missing_export}")

    full = predicted[required_export].copy()
    full = full.sort_values(["xpts", "player_name"], ascending=[False, True])
    aggregated = (
        full.groupby(
            ["element", "fpl_code", "player_name", "team_name", "position", "gameweek"],
            as_index=False,
        )
        .agg(xpts=("xpts", "sum"), expected_minutes=("expected_minutes", "sum"))
        .sort_values(["xpts", "player_name"], ascending=[False, True])
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    full_path = output_dir / f"dastan_gw{gameweek}_fixtures.csv"
    solver_path = output_dir / f"dastan_gw{gameweek}_solver.csv"
    acceptance_path = output_dir / f"dastan_gw{gameweek}_acceptance.json"
    full.to_csv(full_path, index=False)
    aggregated[["element", "gameweek", "xpts", "expected_minutes"]].to_csv(
        solver_path, index=False
    )

    comparison = (
        support.compare_reference(aggregated.to_dict("records"), reference)
        if reference is not None
        else {"available": False, "rows": []}
    )
    parity_gate = evaluate_reference(comparison)
    acceptance = {
        "schema": "dastan-smartplay-free-acceptance-v3",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "season": season,
        "gameweek": gameweek,
        "deadline_time": event.get("deadline_time"),
        "fpl_is_next": bool(event.get("is_next")),
        "fixture_rows": int(len(full)),
        "player_rows": int(len(aggregated)),
        "current_mapping_count": current_mapping_count,
        "upstream": {
            "dastan": "19376523afdec4836d0e6b5632c6773d0fe40c53",
            "smartplay_solver": "7ec56e944982020f8709db5d00b0b78821fb1f38",
            "smartplay_public_mapping": "9b5bec6ae12541be24decd980e119af90617a868",
        },
        "smartplay_reference": comparison,
        "parity_gate": parity_gate,
        "outputs": {"fixtures": str(full_path), "solver": str(solver_path)},
    }
    acceptance_path.write_text(
        json.dumps(acceptance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(aggregated.head(20).to_string(index=False), flush=True)
    if comparison.get("available"):
        print("--- SmartPlay manual spot-check ---", flush=True)
        print(json.dumps(comparison, indent=2, ensure_ascii=False), flush=True)
        print("--- Pre-declared parity gate ---", flush=True)
        print(json.dumps(parity_gate, indent=2, ensure_ascii=False), flush=True)
    print(f"Wrote {solver_path} and {acceptance_path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Free live Dastan GW+1 adapter, refreshed mappings")
    parser.add_argument("--gameweek", type=int, required=True)
    parser.add_argument("--dastan-repo", type=Path, required=True)
    parser.add_argument("--smartplay-public-repo", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--fpl-workers", type=int, default=8)
    args = parser.parse_args()

    dastan_repo = args.dastan_repo.resolve()
    public_repo = args.smartplay_public_repo.resolve()
    if not (dastan_repo / "dastan" / "predictor.py").exists():
        raise RuntimeError(f"not a Dastan checkout: {dastan_repo}")
    if not (public_repo / "data" / "mappings" / "players_golden_record.csv").exists():
        raise RuntimeError(f"not a SmartPlay public-release checkout: {public_repo}")
    sys.path.insert(0, str(dastan_repo))

    from dastan import data as dastan_data
    from dastan import predictor
    import dastan.rebuild.features as feature_module
    from dastan.rebuild.features import build_feature_frame
    from dastan.rebuild.sources import (
        _fetch_understat_fallbacks,
        _merge_understat_players,
        build_canonical_matches,
        download_sources,
        load_understat_player_matches,
        load_understat_team_matches,
    )

    bootstrap = support.fpl_get("bootstrap-static/")
    fixtures = support.fpl_get("fixtures/")
    season = support.season_label(bootstrap["events"])
    event = next(
        (event for event in bootstrap["events"] if int(event["id"]) == args.gameweek), None
    )
    if event is None or not event.get("is_next"):
        raise RuntimeError(f"GW{args.gameweek} is not Official FPL's current is_next event")

    code_to_understat, club_map = load_current_mappings(public_repo, feature_module)
    current_codes = {int(player["code"]) for player in bootstrap["elements"]}
    mapped_current = {code: uid for code, uid in code_to_understat.items() if code in current_codes}
    print(
        f"SmartPlay public mapping: {len(mapped_current)}/{len(current_codes)} current players; "
        f"Coventry={club_map.get('Coventry City')}; Hull={club_map.get('Hull City')}",
        flush=True,
    )

    # Dastan's longest rolling windows are 38 rows. Two completed EPL seasons provide
    # enough history while avoiding needless provider calls for older seasons.
    completed_history = [value for value in dastan_data.SEASONS if value != season][-2:]
    if not completed_history:
        raise RuntimeError("Dastan release exposes no completed historical seasons")

    raw_dir = args.work_dir / "raw"
    print(f"1/7 Dastan historical inputs: {completed_history}", flush=True)
    download_sources(raw_dir, completed_history, workers=12, allow_missing_understat=False)

    print("2/7 Official FPL current-season rows", flush=True)
    summaries = support.fetch_summaries(bootstrap["elements"], workers=args.fpl_workers)
    support.write_current_fpl_core(raw_dir, bootstrap, fixtures, args.gameweek, summaries, season)

    print("3/7 Current Understat data using refreshed public mappings", flush=True)
    current_ids = sorted(set(mapped_current.values()))
    _fetch_understat_fallbacks(
        raw_dir,
        current_ids,
        [season],
        force=True,
        allow_missing=True,
    )

    print("4/7 Historical canonical rows via unmodified Dastan", flush=True)
    historical_players, historical_teams, _ = build_canonical_matches(raw_dir, completed_history)

    print("5/7 Active-season canonical rows via thin identity adapter", flush=True)
    current_players = build_current_canonical(
        raw_dir,
        season,
        code_to_understat,
        feature_module.fpl_to_understat,
        load_understat_player_matches,
        _merge_understat_players,
    )
    current_teams = load_understat_team_matches(raw_dir, [season])
    players = pd.concat([historical_players, current_players], ignore_index=True, sort=False)
    teams = pd.concat([historical_teams, current_teams], ignore_index=True, sort=False)

    team_names = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    teams = support.append_future_team_rows(
        teams,
        fixtures,
        args.gameweek,
        team_names,
        season,
        feature_module.fpl_to_understat,
    )

    print("6/7 Dastan exact feature builder + released weights", flush=True)
    frame = build_feature_frame(players, teams)
    target = frame[
        (frame["season"] == season) & (frame["gameweek"] == args.gameweek)
    ].copy()
    if target.empty:
        raise RuntimeError("feature build produced no target rows")
    context = support.live_context_by_code(bootstrap)
    for column in ("ar_ep_next", "sig_status_risk", "sig_chance_playing", "sig_has_news"):
        target[column] = [context.get(int(code), {}).get(column, -1.0) for code in target["fpl_code"]]

    required = json.loads((dastan_repo / "models" / "feature_cols.json").read_text(encoding="utf-8"))
    missing = [column for column in required if column not in target.columns]
    if missing:
        raise RuntimeError(f"target frame misses Dastan features: {missing[:10]}")
    dastan_data.assert_deadline_anchored(target)
    predicted = predictor.Dastan(dastan_repo / "models").predict_frame(target, with_parts=True)
    predicted = attach_export_metadata(predicted, build_export_lookup(bootstrap))

    print("7/7 Solver-format export + manual SmartPlay parity spot-check", flush=True)
    export_predictions(
        predicted,
        args.gameweek,
        args.output_dir,
        args.reference,
        season,
        event,
        len(mapped_current),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
