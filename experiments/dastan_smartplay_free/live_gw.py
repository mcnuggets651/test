#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

FPL_BASE = "https://fantasy.premierleague.com/api"
STATUS_RISK = {"a": 0, "d": 1, "s": 2, "i": 3, "u": 4, "n": 4}


def _json_get(url: str, *, retries: int = 4, timeout: int = 45) -> Any:
    last: Exception | None = None
    headers = {"User-Agent": "dastan-smartplay-free-prototype/0.1"}
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            time.sleep(2**attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def fpl_get(endpoint: str) -> Any:
    return _json_get(f"{FPL_BASE}/{endpoint.lstrip('/')}")


def season_label(events: list[dict[str, Any]]) -> str:
    deadlines = [
        dt.datetime.fromisoformat(str(event["deadline_time"]).replace("Z", "+00:00"))
        for event in events
        if event.get("deadline_time")
    ]
    if not deadlines:
        raise RuntimeError("FPL bootstrap has no deadlines")
    first = min(deadlines)
    start = first.year if first.month >= 6 else first.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def fetch_summaries(elements: list[dict[str, Any]], workers: int) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    failures: list[tuple[int, str]] = []

    def one(element_id: int) -> tuple[int, dict[str, Any]]:
        return element_id, fpl_get(f"element-summary/{element_id}/")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, int(player["id"])): int(player["id"]) for player in elements}
        for index, future in enumerate(as_completed(futures), 1):
            element_id = futures[future]
            try:
                key, payload = future.result()
                output[key] = payload
            except Exception as exc:  # pragma: no cover - live network boundary
                failures.append((element_id, str(exc)))
            if index % 100 == 0:
                print(f"  FPL summaries {index}/{len(elements)}", flush=True)
    if failures:
        raise RuntimeError(f"failed to fetch {len(failures)} element summaries: {failures[:5]}")
    return output


def current_selected_count(
    player: dict[str, Any],
    bootstrap: dict[str, Any],
    latest_history: dict[str, Any] | None,
) -> float:
    total_players = safe_float(bootstrap.get("total_players"), 0.0)
    percentage = safe_float(player.get("selected_by_percent"), -1.0)
    if total_players > 0 and percentage >= 0:
        return total_players * percentage / 100.0
    if latest_history is not None:
        return safe_float(latest_history.get("selected"), 0.0)
    return 0.0


def synthetic_target_row(
    player: dict[str, Any],
    fixture: dict[str, Any],
    team_names: dict[int, str],
    bootstrap: dict[str, Any],
    latest_history: dict[str, Any] | None,
) -> dict[str, Any]:
    team_id = int(player["team"])
    home = int(fixture["team_h"]) == team_id
    opponent_id = int(fixture["team_a"] if home else fixture["team_h"])
    position = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(
        int(player["element_type"]), "UNK"
    )
    transfers_balance = safe_float(player.get("transfers_in_event")) - safe_float(
        player.get("transfers_out_event")
    )
    return {
        "element": int(player["id"]),
        "fixture": int(fixture["id"]),
        "round": int(fixture["event"]),
        "GW": int(fixture["event"]),
        "position": position,
        "name": str(player.get("web_name") or player.get("second_name") or player["id"]),
        "team": team_names[team_id],
        "opponent_team": opponent_id,
        "was_home": bool(home),
        "kickoff_time": fixture["kickoff_time"],
        "total_points": 0,
        "minutes": 0,
        "goals_scored": 0,
        "assists": 0,
        "goals_conceded": 0,
        "own_goals": 0,
        "penalties_saved": 0,
        "penalties_missed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "saves": 0,
        "bonus": 0,
        "bps": 0,
        "influence": 0.0,
        "creativity": 0.0,
        "threat": 0.0,
        "value": safe_float(player.get("now_cost")),
        "selected": current_selected_count(player, bootstrap, latest_history),
        "transfers_balance": transfers_balance,
        "starts": 0,
        "clearances_blocks_interceptions": 0.0,
        "defensive_contribution": 0.0,
        "recoveries": 0.0,
        "tackles": 0.0,
        "team_h_score": 0,
        "team_a_score": 0,
    }


def normalize_history_row(
    row: dict[str, Any],
    player: dict[str, Any],
    fixture_lookup: dict[int, dict[str, Any]],
    team_names: dict[int, str],
) -> dict[str, Any]:
    fixture = fixture_lookup.get(int(row["fixture"]))
    if fixture is None:
        raise RuntimeError(f"history references unknown fixture {row['fixture']}")
    home = bool(row.get("was_home"))
    team_id = int(fixture["team_h"] if home else fixture["team_a"])
    output = dict(row)
    output["element"] = int(player["id"])
    output["GW"] = int(row.get("round") or fixture.get("event") or 0)
    output["position"] = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(
        int(player["element_type"]), "UNK"
    )
    output["name"] = str(player.get("web_name") or player.get("second_name") or player["id"])
    output["team"] = team_names[team_id]
    output["kickoff_time"] = fixture.get("kickoff_time") or row.get("kickoff_time")
    output.setdefault("starts", None)
    output.setdefault("clearances_blocks_interceptions", None)
    output.setdefault("defensive_contribution", None)
    output.setdefault("recoveries", None)
    output.setdefault("tackles", None)
    output.setdefault("team_h_score", fixture.get("team_h_score"))
    output.setdefault("team_a_score", fixture.get("team_a_score"))
    return output


def write_current_fpl_core(
    raw_dir: Path,
    bootstrap: dict[str, Any],
    fixtures: list[dict[str, Any]],
    target_gw: int,
    summaries: dict[int, dict[str, Any]],
    season: str,
) -> None:
    import pandas as pd

    core = raw_dir / "vaastav" / season
    core.mkdir(parents=True, exist_ok=True)
    elements = bootstrap["elements"]
    team_names = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    fixture_lookup = {int(fixture["id"]): fixture for fixture in fixtures}

    pd.DataFrame([{"id": int(player["id"]), "code": int(player["code"])} for player in elements]).to_csv(
        core / "players_raw.csv", index=False
    )
    pd.DataFrame(bootstrap["teams"]).to_csv(core / "teams.csv", index=False)

    completed: list[dict[str, Any]] = []
    latest_by_element: dict[int, dict[str, Any]] = {}
    for player in elements:
        history = summaries[int(player["id"])].get("history", [])
        eligible = [row for row in history if int(row.get("round") or 0) < target_gw]
        if eligible:
            latest_by_element[int(player["id"])] = max(
                eligible, key=lambda row: int(row.get("round") or 0)
            )
        for row in eligible:
            completed.append(normalize_history_row(row, player, fixture_lookup, team_names))

    target_fixtures = [
        fixture
        for fixture in fixtures
        if int(fixture.get("event") or -1) == target_gw and fixture.get("kickoff_time")
    ]
    if not target_fixtures:
        raise RuntimeError(f"FPL API has no fixtures for GW{target_gw}")
    fixtures_by_team: dict[int, list[dict[str, Any]]] = {}
    for fixture in target_fixtures:
        fixtures_by_team.setdefault(int(fixture["team_h"]), []).append(fixture)
        fixtures_by_team.setdefault(int(fixture["team_a"]), []).append(fixture)

    future_rows: list[dict[str, Any]] = []
    for player in elements:
        for fixture in fixtures_by_team.get(int(player["team"]), []):
            future_rows.append(
                synthetic_target_row(
                    player,
                    fixture,
                    team_names,
                    bootstrap,
                    latest_by_element.get(int(player["id"])),
                )
            )

    pd.DataFrame(completed + future_rows).to_csv(core / "merged_gw.csv", index=False)
    print(
        f"  current FPL core: {len(completed)} completed player-fixtures + "
        f"{len(future_rows)} GW{target_gw} target rows",
        flush=True,
    )


def append_future_team_rows(
    teams,
    fixtures: list[dict[str, Any]],
    target_gw: int,
    team_names: dict[int, str],
    season: str,
    fpl_to_understat,
):
    import pandas as pd

    target = [
        fixture
        for fixture in fixtures
        if int(fixture.get("event") or -1) == target_gw and fixture.get("kickoff_time")
    ]
    rows: list[dict[str, Any]] = []
    for index, fixture in enumerate(sorted(target, key=lambda item: int(item["id"]))):
        # Dastan's public team feature builder reconstructs opponents from result/xG
        # symmetry. These unique paired signatures make simultaneous FUTURE rows pair
        # deterministically. Their raw values are shifted out before the target row's
        # rolling features are used, so they cannot leak invented future performance.
        a = 0.001 * (index + 1)
        b = 0.501 + 0.001 * index
        home = fpl_to_understat(team_names[int(fixture["team_h"])])
        away = fpl_to_understat(team_names[int(fixture["team_a"])])
        common = {
            "season": season,
            "date": fixture["kickoff_time"],
            "scored": 0,
            "missed": 0,
            "deep": 0.0,
            "deep_allowed": 0.0,
            "ppda_att": 0.0,
            "ppda_def": 0.0,
            "ppda_allowed_att": 0.0,
            "ppda_allowed_def": 0.0,
            "pts": 0.0,
        }
        rows.append({**common, "understat_team": home, "is_home": 1.0, "xG": a, "xGA": b})
        rows.append({**common, "understat_team": away, "is_home": 0.0, "xG": b, "xGA": a})
    if not rows:
        raise RuntimeError(f"no future team rows for GW{target_gw}")
    return pd.concat([teams, pd.DataFrame(rows)], ignore_index=True)


def live_context_by_code(bootstrap: dict[str, Any]) -> dict[int, dict[str, float]]:
    output: dict[int, dict[str, float]] = {}
    for player in bootstrap["elements"]:
        chance = player.get("chance_of_playing_next_round")
        ep_next = player.get("ep_next")
        output[int(player["code"])] = {
            "ar_ep_next": -1.0 if ep_next is None or ep_next == "" else safe_float(ep_next, -1.0),
            "sig_status_risk": float(STATUS_RISK.get(str(player.get("status") or "a"), 0)),
            "sig_chance_playing": -1.0 if chance is None else safe_float(chance, -1.0),
            "sig_has_news": float(bool(str(player.get("news") or "").strip())),
        }
    return output


def normalize_name(value: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return "".join(character for character in text.casefold() if character.isalnum())


def compare_reference(output_rows: list[dict[str, Any]], reference_path: Path) -> dict[str, Any]:
    import pandas as pd

    if not reference_path.exists():
        return {"available": False, "rows": []}
    reference = pd.read_csv(reference_path)
    predictions = pd.DataFrame(output_rows)
    predictions["norm"] = predictions["player_name"].astype(str).map(normalize_name)
    reference["norm"] = reference["player_name"].astype(str).map(normalize_name)
    merged = reference.merge(predictions[["norm", "xpts"]], on="norm", how="left")
    merged["delta"] = merged["xpts"] - merged["smartplay_xpts"]
    rows = merged[["player_name", "smartplay_xpts", "xpts", "delta"]].to_dict("records")
    valid = merged["xpts"].notna()
    mae = float(merged.loc[valid, "delta"].abs().mean()) if valid.any() else None
    return {
        "available": True,
        "matched": int(valid.sum()),
        "total": int(len(merged)),
        "mae": mae,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Free local Dastan -> SmartPlay Solver projection adapter"
    )
    parser.add_argument("--gameweek", type=int, required=True)
    parser.add_argument("--dastan-repo", type=Path, required=True)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path.home() / ".cache" / "dastan-smartplay-free",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--fpl-workers", type=int, default=8)
    args = parser.parse_args()

    dastan_repo = args.dastan_repo.resolve()
    if not (dastan_repo / "dastan" / "predictor.py").exists():
        raise RuntimeError(f"not a Dastan checkout: {dastan_repo}")
    sys.path.insert(0, str(dastan_repo))

    from dastan import data as dastan_data
    from dastan import mappings, predictor
    from dastan.rebuild.features import build_feature_frame, fpl_to_understat
    from dastan.rebuild.sources import (
        _fetch_understat_fallbacks,
        build_canonical_matches,
        download_sources,
    )

    bootstrap = fpl_get("bootstrap-static/")
    fixtures = fpl_get("fixtures/")
    season = season_label(bootstrap["events"])
    event = next(
        (event for event in bootstrap["events"] if int(event["id"]) == args.gameweek),
        None,
    )
    if event is None:
        raise RuntimeError(f"GW{args.gameweek} not found")
    if not event.get("is_next"):
        raise RuntimeError(
            f"GW{args.gameweek} is not FPL's current is_next event; refusing a misleading live acceptance run"
        )

    print(f"Building live Dastan GW{args.gameweek} for {season}", flush=True)
    raw_dir = args.work_dir / "raw"
    release_history = [value for value in dastan_data.SEASONS if value != season]

    print("1/6 Downloading/caching Dastan's pinned historical public inputs", flush=True)
    download_sources(raw_dir, release_history, workers=12, allow_missing_understat=False)

    print("2/6 Fetching current Official FPL history and target fixtures", flush=True)
    summaries = fetch_summaries(bootstrap["elements"], workers=args.fpl_workers)
    write_current_fpl_core(raw_dir, bootstrap, fixtures, args.gameweek, summaries, season)

    print(
        "3/6 Fetching current Understat histories through Dastan's own public source adapter",
        flush=True,
    )
    assignments = mappings.assignments_for_seasons([season])
    current_ids = sorted(
        {int(value) for value in assignments["understat_id"].dropna().tolist()}
    )
    _fetch_understat_fallbacks(
        raw_dir,
        current_ids,
        [season],
        force=True,
        allow_missing=True,
    )

    print("4/6 Building Dastan's exact public feature frame", flush=True)
    seasons = [*release_history, season]
    players, teams, _lookup = build_canonical_matches(raw_dir, seasons)
    team_names = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    teams = append_future_team_rows(
        teams,
        fixtures,
        args.gameweek,
        team_names,
        season,
        fpl_to_understat,
    )
    frame = build_feature_frame(players, teams)
    target = frame[
        (frame["season"] == season) & (frame["gameweek"] == args.gameweek)
    ].copy()
    if target.empty:
        raise RuntimeError("Dastan feature build produced no target rows")

    context = live_context_by_code(bootstrap)
    for column in (
        "ar_ep_next",
        "sig_status_risk",
        "sig_chance_playing",
        "sig_has_news",
    ):
        target[column] = [
            context.get(int(code), {}).get(column, -1.0) for code in target["fpl_code"]
        ]

    required_features = json.loads(
        (dastan_repo / "models" / "feature_cols.json").read_text(encoding="utf-8")
    )
    missing = [column for column in required_features if column not in target.columns]
    if missing:
        raise RuntimeError(f"live target frame misses Dastan features: {missing[:10]}")
    dastan_data.assert_deadline_anchored(target)

    print("5/6 Running Dastan's released weights", flush=True)
    predicted = predictor.Dastan(dastan_repo / "models").predict_frame(
        target, with_parts=True
    )
    full = predicted[
        [
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
    ].copy()
    full = full.sort_values(["xpts", "player_name"], ascending=[False, True])
    aggregated = (
        full.groupby(
            [
                "element",
                "fpl_code",
                "player_name",
                "team_name",
                "position",
                "gameweek",
            ],
            as_index=False,
        )
        .agg(xpts=("xpts", "sum"), expected_minutes=("expected_minutes", "sum"))
        .sort_values(["xpts", "player_name"], ascending=[False, True])
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    full_path = args.output_dir / f"dastan_gw{args.gameweek}_fixtures.csv"
    solver_path = args.output_dir / f"dastan_gw{args.gameweek}_solver.csv"
    metadata_path = args.output_dir / f"dastan_gw{args.gameweek}_acceptance.json"
    full.to_csv(full_path, index=False)
    aggregated[["element", "gameweek", "xpts", "expected_minutes"]].to_csv(
        solver_path, index=False
    )

    reference = (
        compare_reference(aggregated.to_dict("records"), args.reference)
        if args.reference
        else {"available": False, "rows": []}
    )
    metadata = {
        "schema": "dastan-smartplay-free-acceptance-v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "season": season,
        "gameweek": args.gameweek,
        "deadline_time": event.get("deadline_time"),
        "fpl_is_next": bool(event.get("is_next")),
        "fixture_rows": int(len(full)),
        "player_rows": int(len(aggregated)),
        "understat_mapping_ids": len(current_ids),
        "upstream": {
            "dastan_expected_commit": "19376523afdec4836d0e6b5632c6773d0fe40c53",
            "smartplay_solver_expected_commit": "7ec56e944982020f8709db5d00b0b78821fb1f38",
        },
        "smartplay_reference": reference,
        "outputs": {"fixtures": str(full_path), "solver": str(solver_path)},
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print("6/6 Acceptance output", flush=True)
    print(aggregated.head(20).to_string(index=False), flush=True)
    if reference.get("available"):
        print(json.dumps(reference, indent=2, ensure_ascii=False), flush=True)
    print(f"Wrote {solver_path} and {metadata_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
