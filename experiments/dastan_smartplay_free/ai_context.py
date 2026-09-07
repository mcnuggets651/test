#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

AI_CONTEXT_SCHEMA = "dastan-smartplay-ai-context-v1"
STRATEGY_SCHEMA = "dastan-smartplay-free-strategy-v2"
PRIVATE_SCOPE = "PRIVATE_MANAGER"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def load_csv(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ValueError(f"could not read {label}: {path} ({exc})") from exc
    if not rows:
        raise ValueError(f"{label} is empty: {path}")
    return rows


def as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc


def maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def atomic_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def git_toplevel(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_private_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    run = snapshot.get("run")
    state = snapshot.get("team_state")
    if not isinstance(run, dict) or not isinstance(state, dict):
        raise ValueError("private snapshot must contain run and team_state objects")
    if run.get("attestation_scope") != PRIVATE_SCOPE or run.get("immutable") is not True:
        raise ValueError("AI context requires an immutable PRIVATE_MANAGER snapshot")
    if state.get("state_complete_for_transfers") is not True:
        raise ValueError("AI context requires transfer-complete owner state")
    if state.get("active_chip") not in (None, "", False):
        raise ValueError("AI context refuses active-chip state for this no-chip GW+1 engine")

    entry_id = as_int(state.get("entry_id"), "team_state.entry_id")
    gameweek = as_int(run.get("target_gameweek"), "run.target_gameweek")
    published_gw = as_int(state.get("published_gw"), "team_state.published_gw")
    bank_tenths = as_int(state.get("bank_tenths"), "team_state.bank_tenths")
    free_transfers = as_int(state.get("free_transfers"), "team_state.free_transfers")
    squad = state.get("squad")
    if not isinstance(squad, list) or len(squad) != 15:
        raise ValueError("AI context requires exactly 15 squad rows")

    clean_squad: list[dict[str, Any]] = []
    seen: set[int] = set()
    sell_value = 0
    for index, row in enumerate(squad, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"squad row {index} must be an object")
        element = as_int(row.get("element_id"), f"squad row {index} element_id")
        purchase = as_int(row.get("purchase_price_tenths"), f"squad row {index} purchase_price_tenths")
        selling = as_int(row.get("selling_price_tenths"), f"squad row {index} selling_price_tenths")
        if element <= 0 or purchase <= 0 or selling <= 0:
            raise ValueError(f"squad row {index} has invalid identity/prices")
        if element in seen:
            raise ValueError("AI context requires 15 unique squad elements")
        seen.add(element)
        sell_value += selling
        clean_squad.append(
            {
                "element": element,
                "name": first(row, "web_name", "name", "player_name"),
                "team": first(row, "team_name", "team"),
                "position": first(row, "position", "position_name", "element_type"),
                "purchase_price_tenths": purchase,
                "selling_price_tenths": selling,
                "current_price_tenths": maybe_int(
                    first(
                        row,
                        "current_price_tenths_at_snapshot",
                        "now_cost_tenths",
                        "current_price_tenths",
                        "now_cost",
                    )
                ),
            }
        )

    owner = {
        "entry_id": entry_id,
        "published_gameweek": published_gw,
        "target_gameweek": gameweek,
        "bank_tenths": bank_tenths,
        "bank_millions": round(bank_tenths / 10.0, 1),
        "free_transfers": free_transfers,
        "active_chip": None,
        "squad_sell_value_tenths": sell_value,
        "transfer_budget_tenths": sell_value + bank_tenths,
        "transfer_budget_millions": round((sell_value + bank_tenths) / 10.0, 1),
    }
    release = {
        "run_id": run.get("run_id"),
        "release_tag": run.get("release_tag"),
        "published_at": run.get("published_at"),
        "attestation_scope": PRIVATE_SCOPE,
        "immutable": True,
    }
    return owner, clean_squad, release


def validate_strategy_inputs(state_dir: Path, snapshot_path: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    if manifest.get("schema") != STRATEGY_SCHEMA:
        raise ValueError(f"unexpected strategy manifest schema: {manifest.get('schema')!r}")
    gameweek = as_int(manifest.get("gameweek"), "strategy_manifest.gameweek")
    state = manifest.get("team_state") or {}
    if not isinstance(state, dict) or state.get("source_sha256") != sha256_file(snapshot_path):
        raise ValueError("strategy manifest is not SHA-256-bound to the supplied private snapshot")

    projection_dir = state_dir / "projections"
    solution_dir = state_dir / "solution"
    paths = {
        "acceptance": projection_dir / f"dastan_gw{gameweek}_acceptance.json",
        "solver_csv": projection_dir / f"dastan_gw{gameweek}_solver.csv",
        "fixtures_csv": projection_dir / f"dastan_gw{gameweek}_fixtures.csv",
        "summary": solution_dir / "summary.md",
        "picks_csv": solution_dir / "picks.csv",
        "solution_json": solution_dir / "solution.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"AI context source files are missing: {missing}")

    inputs = manifest.get("inputs") or {}
    solution = manifest.get("solution") or {}
    expected = {
        "acceptance": inputs.get("projection_acceptance_sha256"),
        "solver_csv": inputs.get("projection_csv_sha256"),
        "fixtures_csv": inputs.get("projection_fixtures_sha256"),
        "summary": solution.get("summary_sha256"),
        "picks_csv": solution.get("picks_sha256"),
        "solution_json": solution.get("json_sha256"),
    }
    for name, expected_hash in expected.items():
        if not isinstance(expected_hash, str) or sha256_file(paths[name]) != expected_hash:
            raise ValueError(f"{name} hash does not match strategy manifest")
    return paths


def projection_evidence(
    solver_rows: list[dict[str, str]], fixture_rows: list[dict[str, str]], gameweek: int
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    fixtures_by_element: dict[int, list[dict[str, Any]]] = {}
    metadata: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(fixture_rows, start=1):
        element = as_int(row.get("element"), f"fixture row {index} element")
        if as_int(row.get("gameweek"), f"fixture row {index} gameweek") != gameweek:
            raise ValueError(f"fixture row {index} gameweek mismatch")
        fixtures_by_element.setdefault(element, []).append(
            {
                "fixture": row.get("fixture"),
                "kickoff_time": row.get("kickoff_time"),
                "xpts": maybe_float(row.get("xpts")),
                "expected_minutes": maybe_float(row.get("expected_minutes")),
                "p60": maybe_float(row.get("p60")),
                "p_any": maybe_float(row.get("p_any")),
            }
        )
        metadata.setdefault(
            element,
            {"name": row.get("player_name"), "team": row.get("team_name"), "position": row.get("position")},
        )

    players: list[dict[str, Any]] = []
    by_element: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(solver_rows, start=1):
        element = as_int(row.get("element"), f"solver row {index} element")
        if as_int(row.get("gameweek"), f"solver row {index} gameweek") != gameweek:
            raise ValueError(f"solver row {index} gameweek mismatch")
        if element in by_element:
            raise ValueError(f"solver projection contains duplicate element {element}")
        xpts = maybe_float(row.get("xpts"))
        xmins = maybe_float(row.get("expected_minutes"))
        if xpts is None or xmins is None:
            raise ValueError(f"solver row {index} has invalid xpts/expected_minutes")
        meta = metadata.get(element, {})
        item = {
            "element": element,
            "name": meta.get("name"),
            "team": meta.get("team"),
            "position": meta.get("position"),
            "xpts": xpts,
            "expected_minutes": xmins,
            "fixture_count": len(fixtures_by_element.get(element, [])),
            "fixtures": fixtures_by_element.get(element, []),
        }
        players.append(item)
        by_element[element] = item
    players.sort(key=lambda row: (-float(row["xpts"]), str(row.get("name") or "")))
    return players, by_element


def enrich_current_squad(squad: list[dict[str, Any]], projections: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in squad:
        projection = projections.get(int(row["element"]), {})
        enriched.append(
            {
                **row,
                "name": row.get("name") or projection.get("name"),
                "team": row.get("team") or projection.get("team"),
                "position": row.get("position") or projection.get("position"),
                "xpts": projection.get("xpts"),
                "expected_minutes": projection.get("expected_minutes"),
                "fixtures": projection.get("fixtures", []),
            }
        )
    return enriched


def solution_player(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "element": maybe_int(row.get("id")),
        "name": row.get("name"),
        "team": row.get("team"),
        "position": row.get("pos"),
        "xpts": maybe_float(row.get("xP")),
        "expected_minutes": maybe_float(row.get("xMin")),
        "captain": bool(maybe_int(row.get("captain")) or 0),
        "vice_captain": bool(maybe_int(row.get("vicecaptain")) or 0),
        "bench_slot": maybe_int(row.get("bench")),
        "buy_price_millions": maybe_float(row.get("buy_price")),
        "sell_price_millions": maybe_float(row.get("sell_price")),
    }


def normalize_solution(solution: dict[str, Any], gameweek: int) -> dict[str, Any]:
    picks = solution.get("picks")
    if not isinstance(picks, list) or not picks:
        raise ValueError("solution.json is missing picks")
    rows = [row for row in picks if isinstance(row, dict) and maybe_int(row.get("week")) == gameweek]
    if not rows:
        raise ValueError(f"solution.json contains no GW{gameweek} picks")

    transfers_in = [solution_player(row) for row in rows if (maybe_int(row.get("transfer_in")) or 0) == 1]
    transfers_out = [solution_player(row) for row in rows if (maybe_int(row.get("transfer_out")) or 0) == 1]
    starting_xi = [solution_player(row) for row in rows if (maybe_int(row.get("lineup")) or 0) == 1]
    bench = [
        solution_player(row)
        for row in rows
        if (maybe_int(row.get("squad")) or 0) == 1
        and maybe_int(row.get("bench")) is not None
        and int(float(row.get("bench"))) >= 0
    ]
    bench.sort(key=lambda row: int(row.get("bench_slot") or 0))
    captain = next((row for row in starting_xi if row["captain"]), None)
    vice = next((row for row in starting_xi if row["vice_captain"]), None)

    stats: dict[str, Any] = {}
    statistics = solution.get("statistics")
    if isinstance(statistics, dict):
        candidate = statistics.get(str(gameweek), statistics.get(gameweek))
        if isinstance(candidate, dict):
            stats = candidate

    return {
        "transfers": {
            "in": transfers_in,
            "out": transfers_out,
            "count": maybe_int(stats.get("nt")) if stats else len(transfers_in),
            "penalized_transfers": maybe_int(stats.get("pt")) if stats else None,
        },
        "starting_xi": starting_xi,
        "captain": captain,
        "vice_captain": vice,
        "bench": bench,
        "bank_after_millions": maybe_float(stats.get("itb")),
        "free_transfers_after": maybe_int(stats.get("ft")),
        "solver_total_xp": maybe_float(solution.get("total_xp")),
        "solver_statistics_xp": maybe_float(stats.get("xP")),
        "objective_score": maybe_float(solution.get("score")),
        "chip": stats.get("chip") if stats else None,
        "solver_meta": solution.get("meta") if isinstance(solution.get("meta"), dict) else {},
        "summary": solution.get("summary"),
    }


def ai_contract() -> dict[str, Any]:
    return {
        "mode": "quantitative_anchor_plus_ai_interpretation",
        "hard_rules": [
            "Dastan xPts and expected minutes are immutable model evidence; never rewrite them.",
            "SmartPlay Solver output is immutable optimizer evidence; never present an AI preference as the solver result.",
            "Never invent a numerical edge for a counterfactual route that has not been deterministically solved.",
            "Keep the certified projection horizon at GW+1; do not fabricate future Dastan xPts.",
            "Label news, injuries, European minutes, lineups, set pieces, price signals and manager quotes as external evidence with source and timestamp.",
            "Separate model result, external evidence, AI interpretation and final recommendation.",
            "If external evidence conflicts with model assumptions, explain the conflict rather than silently changing model numbers.",
            "Do not execute transfers or other account actions from this context.",
        ],
        "recommended_interpretation_order": [
            "Verify provenance, integrity and target gameweek.",
            "State the raw Dastan + SmartPlay recommendation and exact model numbers.",
            "Inspect squad xPts/minutes, captaincy, transfer budget and bench.",
            "Add current external evidence only after stating the quantitative result.",
            "Challenge one-week artifacts such as premium concentration, European congestion, weak bench slots and recovery difficulty.",
            "Mark unsolved structural alternatives as hypotheses until a deterministic scenario solve exists.",
            "Give one final recommendation with confidence and reversal conditions.",
        ],
        "external_evidence_not_embedded": [
            "breaking injuries and illness",
            "press conferences and manager quotes",
            "European/domestic-cup minutes after model generation",
            "late tactical and set-piece role changes",
            "live transfer-market and price-change intelligence",
        ],
    }


def build_bundle(state_dir: Path, snapshot_path: Path) -> dict[str, Any]:
    state_dir = state_dir.expanduser().resolve()
    snapshot_path = snapshot_path.expanduser().resolve()
    snapshot = load_json_object(snapshot_path, "private strategy snapshot")
    owner, squad, release = validate_private_snapshot(snapshot)

    manifest_path = state_dir / "strategy_manifest.json"
    manifest = load_json_object(manifest_path, "strategy manifest")
    paths = validate_strategy_inputs(state_dir, snapshot_path, manifest)
    gameweek = as_int(manifest.get("gameweek"), "strategy_manifest.gameweek")
    if gameweek != owner["target_gameweek"]:
        raise ValueError("strategy gameweek does not match private owner-state target gameweek")

    acceptance = load_json_object(paths["acceptance"], "Dastan acceptance")
    if as_int(acceptance.get("gameweek"), "acceptance.gameweek") != gameweek or acceptance.get("fpl_is_next") is not True:
        raise ValueError("Dastan acceptance is not for the exact Official-FPL next gameweek")

    all_players, projection_map = projection_evidence(
        load_csv(paths["solver_csv"], "Dastan solver projection CSV"),
        load_csv(paths["fixtures_csv"], "Dastan fixture projection CSV"),
        gameweek,
    )
    solution = load_json_object(paths["solution_json"], "SmartPlay solution")

    return {
        "schema": AI_CONTEXT_SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "privacy": {
            "classification": "PRIVATE_MANAGER_LOCAL_ONLY",
            "contains_exact_owner_state": True,
            "safe_for_public_artifact_upload": False,
            "retention": "local output directory only",
        },
        "scope": {
            "entry_id": owner["entry_id"],
            "gameweek": gameweek,
            "projection_horizon": 1,
            "model": "open Dastan",
            "optimizer": "open SmartPlay Solver",
            "purpose": "AI interpretation of fixed quantitative evidence",
        },
        "owner_state": owner,
        "current_squad": enrich_current_squad(squad, projection_map),
        "model_evidence": {
            "player_count": len(all_players),
            "all_players": all_players,
            "top_30_by_xpts": all_players[:30],
            "acceptance": {
                "schema": acceptance.get("schema"),
                "generated_at": acceptance.get("generated_at"),
                "season": acceptance.get("season"),
                "deadline_time": acceptance.get("deadline_time"),
                "fpl_is_next": acceptance.get("fpl_is_next"),
                "fixture_rows": acceptance.get("fixture_rows"),
                "player_rows": acceptance.get("player_rows"),
                "current_mapping_count": acceptance.get("current_mapping_count"),
                "parity_gate": acceptance.get("parity_gate"),
            },
        },
        "optimizer_evidence": normalize_solution(solution, gameweek),
        "private_authority": release,
        "integrity": {
            "private_snapshot_sha256": sha256_file(snapshot_path),
            "strategy_manifest_sha256": sha256_file(manifest_path),
            "projection_acceptance_sha256": sha256_file(paths["acceptance"]),
            "projection_solver_csv_sha256": sha256_file(paths["solver_csv"]),
            "projection_fixtures_csv_sha256": sha256_file(paths["fixtures_csv"]),
            "solution_json_sha256": sha256_file(paths["solution_json"]),
            "solution_picks_csv_sha256": sha256_file(paths["picks_csv"]),
            "solution_summary_sha256": sha256_file(paths["summary"]),
            "upstream": manifest.get("upstream"),
            "scientific_boundary": manifest.get("scientific_boundary"),
        },
        "ai_contract": ai_contract(),
    }


def render_brief(bundle: dict[str, Any]) -> str:
    owner = bundle["owner_state"]
    decision = bundle["optimizer_evidence"]
    lines = [
        "# Dastan + SmartPlay AI Decision Brief",
        "",
        f"- Entry: {owner['entry_id']}",
        f"- Gameweek: {owner['target_gameweek']}",
        f"- Free transfers before solve: {owner['free_transfers']}",
        f"- Bank before solve: £{owner['bank_millions']:.1f}m",
        "- Model horizon: GW+1 only",
        f"- Solver total xP: {decision.get('solver_total_xp')}",
        f"- Solver objective: {decision.get('objective_score')}",
        "",
        "## Transfers",
    ]
    transfers_in = decision["transfers"]["in"]
    transfers_out = decision["transfers"]["out"]
    if transfers_in or transfers_out:
        for row in transfers_out:
            lines.append(f"- Sell: {row.get('name')}")
        for row in transfers_in:
            lines.append(f"- Buy: {row.get('name')}")
    else:
        lines.append("- Roll / no transfer")

    captain = decision.get("captain") or {}
    vice = decision.get("vice_captain") or {}
    lines += ["", "## Captaincy", f"- Captain: {captain.get('name')}", f"- Vice-captain: {vice.get('name')}", "", "## Starting XI"]
    for row in decision.get("starting_xi") or []:
        suffix = " (C)" if row.get("captain") else " (VC)" if row.get("vice_captain") else ""
        lines.append(f"- {row.get('name')}: {row.get('xpts')} xP{suffix}")
    lines += ["", "## Bench"]
    for row in decision.get("bench") or []:
        lines.append(f"- {row.get('name')}: {row.get('xpts')} xP")
    lines += [
        "",
        "## AI interpretation contract",
        "- Treat Dastan xP/minutes and SmartPlay output as immutable evidence.",
        "- Add news/European minutes/roles only as separately sourced external evidence.",
        "- Do not invent numerical counterfactual edges or future-GW Dastan projections.",
        "- Separate model result, external evidence, AI interpretation and final recommendation.",
        "",
        "Attach `ai_decision_context.json` to ChatGPT and ask:",
        "",
        "> Interpret this Dastan + SmartPlay decision for the current FPL deadline. Use the model numbers as fixed evidence, research current external context, challenge one-week structural weaknesses, and give one final recommendation. Do not invent unsolved numerical counterfactuals.",
        "",
    ]
    return "\n".join(lines)


def write_bundle(state_dir: Path, snapshot_path: Path) -> dict[str, Any]:
    state_dir = state_dir.expanduser().resolve()
    context = state_dir / "ai_decision_context.json"
    brief = state_dir / "ai_decision_brief.md"
    checksum = state_dir / "ai_decision_context.sha256"
    bundle = build_bundle(state_dir, snapshot_path)
    atomic_private_text(context, json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    digest = sha256_file(context)
    atomic_private_text(checksum, f"{digest}  {context.name}\n")
    atomic_private_text(brief, render_brief(bundle))
    return {
        "schema": AI_CONTEXT_SCHEMA,
        "status": "ready",
        "context_file": context.name,
        "context_sha256": digest,
        "checksum_file": checksum.name,
        "brief_file": brief.name,
        "brief_sha256": sha256_file(brief),
        "privacy": "PRIVATE_MANAGER_LOCAL_ONLY",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a private read-only AI interpretation bundle from a completed Dastan + SmartPlay solve")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    repo_root = git_toplevel(root)
    state_dir = args.state_dir.expanduser().resolve()
    if repo_root is not None and is_within(state_dir, repo_root):
        parser.error("AI decision context contains private owner state and must remain outside the Git worktree")
    metadata = write_bundle(state_dir, args.snapshot)
    print(f"AI decision context: {state_dir / metadata['context_file']}")
    print(f"AI decision brief: {state_dir / metadata['brief_file']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        raise SystemExit(2)
