#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

DASTAN_SHA = "19376523afdec4836d0e6b5632c6773d0fe40c53"
SOLVER_SHA = "7ec56e944982020f8709db5d00b0b78821fb1f38"
SMARTPLAY_PUBLIC_SHA = "9b5bec6ae12541be24decd980e119af90617a868"
DEFAULT_ENTRY_ID = 63984
MANIFEST_SCHEMA = "dastan-smartplay-free-strategy-v2"
GW1_MIN_EXPECTED_MINUTES = 1


def run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def git_toplevel(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def stack_ready(root: Path) -> bool:
    vendor = root / ".vendor"
    venv = root / ".venv"
    return bool(
        (venv / "bin" / "python").exists()
        and (venv / "bin" / "smartplay-solver").exists()
        and git_head(vendor / "smartplayfpl-dastan") == DASTAN_SHA
        and git_head(vendor / "smartplayfpl-solver") == SOLVER_SHA
        and git_head(vendor / "smartplayfpl-public") == SMARTPLAY_PUBLIC_SHA
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fpl_bootstrap() -> dict[str, Any]:
    request = urllib.request.Request(
        "https://fantasy.premierleague.com/api/bootstrap-static/",
        headers={"User-Agent": "dastan-smartplay-free/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Official FPL bootstrap-static returned a non-object payload")
    return payload


def next_gameweek(bootstrap: dict[str, Any]) -> int:
    candidates = [int(event["id"]) for event in bootstrap.get("events", []) if event.get("is_next")]
    if len(candidates) != 1:
        raise RuntimeError(f"Official FPL returned {len(candidates)} is_next events")
    return candidates[0]


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def reusable_acceptance(path: Path, gameweek: int, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated = parse_time(str(payload["generated_at"]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
    age = dt.datetime.now(dt.timezone.utc) - generated.astimezone(dt.timezone.utc)
    return bool(
        payload.get("schema") == "dastan-smartplay-free-acceptance-v3"
        and int(payload.get("gameweek") or 0) == gameweek
        and payload.get("fpl_is_next") is True
        and dt.timedelta(0) <= age <= dt.timedelta(hours=max_age_hours)
        and (payload.get("upstream") or {}).get("dastan") == DASTAN_SHA
        and (payload.get("upstream") or {}).get("smartplay_solver") == SOLVER_SHA
        and (payload.get("upstream") or {}).get("smartplay_public_mapping") == SMARTPLAY_PUBLIC_SHA
    )


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if isinstance(value, float) and value != converted:
        raise ValueError(f"{name} must be an integer")
    return converted


def validate_solver_team(team: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(team, dict):
        raise ValueError("team JSON must contain an object")
    picks = team.get("picks")
    if not isinstance(picks, list) or len(picks) != 15:
        raise ValueError("exact team state must contain exactly 15 picks")

    normalized_picks: list[dict[str, Any]] = []
    elements: list[int] = []
    for index, raw in enumerate(picks, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"pick {index} must be an object")
        element = _as_int(raw.get("element"), f"pick {index} element")
        purchase = _as_int(raw.get("purchase_price"), f"pick {index} purchase_price")
        selling = _as_int(raw.get("selling_price"), f"pick {index} selling_price")
        if element <= 0:
            raise ValueError(f"pick {index} element must be positive")
        if purchase <= 0 or selling <= 0:
            raise ValueError(f"pick {index} purchase/selling prices must be positive tenths")
        elements.append(element)
        normalized_picks.append(
            {
                **raw,
                "element": element,
                "position": _as_int(raw.get("position", index), f"pick {index} position"),
                "purchase_price": purchase,
                "selling_price": selling,
                "multiplier": _as_int(raw.get("multiplier", 1), f"pick {index} multiplier"),
                "is_captain": bool(raw.get("is_captain", False)),
                "is_vice_captain": bool(raw.get("is_vice_captain", False)),
            }
        )
    if len(set(elements)) != 15:
        raise ValueError("exact team state must contain 15 unique elements")

    transfers = team.get("transfers")
    if not isinstance(transfers, dict):
        raise ValueError("exact team state must contain a transfers object")
    bank = _as_int(transfers.get("bank"), "transfers.bank")
    free_transfers = _as_int(transfers.get("limit"), "transfers.limit")
    if bank < 0:
        raise ValueError("transfers.bank must be non-negative tenths")
    if not 0 <= free_transfers <= 5:
        raise ValueError("transfers.limit must be between 0 and 5")

    normalized = dict(team)
    normalized["picks"] = normalized_picks
    normalized["transfers"] = {
        **transfers,
        "bank": bank,
        "limit": free_transfers,
        "made": 0,
        "cost": 0,
        "status": "cost",
        "value": sum(int(p["selling_price"]) for p in normalized_picks),
    }
    chips = normalized.get("chips")
    if chips is None:
        normalized["chips"] = []
    elif not isinstance(chips, list):
        raise ValueError("team chips must be a list when present")
    return normalized


def solver_team_from_apex_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(snapshot, dict):
        raise ValueError("Apex strategy snapshot must contain an object")
    run_meta = snapshot.get("run") or {}
    team_state = snapshot.get("team_state") or {}
    if not isinstance(run_meta, dict) or not isinstance(team_state, dict):
        raise ValueError("Apex strategy snapshot is missing run/team_state objects")
    if run_meta.get("attestation_scope") != "PRIVATE_MANAGER":
        raise ValueError("Apex strategy snapshot is not PRIVATE_MANAGER-attested")
    if run_meta.get("immutable") is not True:
        raise ValueError("Apex strategy snapshot is not marked immutable")
    if team_state.get("state_complete_for_transfers") is not True:
        raise ValueError("Apex TeamState is not complete for transfers")
    if team_state.get("active_chip") not in (None, "", False):
        raise ValueError("Apex TeamState has an active chip; this no-chip GW+1 runner refuses to ignore it")

    entry_id = _as_int(team_state.get("entry_id"), "team_state.entry_id")
    published_gw = _as_int(team_state.get("published_gw"), "team_state.published_gw")
    target_gameweek = _as_int(run_meta.get("target_gameweek"), "run.target_gameweek")
    if target_gameweek <= published_gw:
        raise ValueError("Apex strategy snapshot target gameweek must be after its published gameweek")
    bank_tenths = _as_int(team_state.get("bank_tenths"), "team_state.bank_tenths")
    free_transfers = _as_int(team_state.get("free_transfers"), "team_state.free_transfers")
    squad = team_state.get("squad")
    if not isinstance(squad, list) or len(squad) != 15:
        raise ValueError("Apex TeamState must contain exactly 15 squad rows")

    picks: list[dict[str, Any]] = []
    for index, row in enumerate(squad, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Apex squad row {index} must be an object")
        picks.append(
            {
                "element": _as_int(row.get("element_id"), f"Apex squad row {index} element_id"),
                "position": index,
                "selling_price": _as_int(
                    row.get("selling_price_tenths"), f"Apex squad row {index} selling_price_tenths"
                ),
                "purchase_price": _as_int(
                    row.get("purchase_price_tenths"), f"Apex squad row {index} purchase_price_tenths"
                ),
                "multiplier": 1,
                "is_captain": False,
                "is_vice_captain": False,
            }
        )

    team = validate_solver_team(
        {
            "picks": picks,
            "transfers": {
                "bank": bank_tenths,
                "limit": free_transfers,
                "made": 0,
                "cost": 0,
                "status": "cost",
                "value": sum(int(p["selling_price"]) for p in picks),
            },
            "chips": [],
            "_meta": {
                "entry_id": entry_id,
                "source": "apex_private_strategy_snapshot",
                "published_gw": published_gw,
                "target_gameweek": target_gameweek,
                "apex_run_id": run_meta.get("run_id"),
                "apex_release_tag": run_meta.get("release_tag"),
                "apex_published_at": run_meta.get("published_at"),
            },
        }
    )
    provenance = {
        "mode": "apex_private_strategy_snapshot",
        "entry_id": entry_id,
        "published_gw": published_gw,
        "target_gameweek": target_gameweek,
        "bank_tenths": bank_tenths,
        "free_transfers": free_transfers,
        "apex_run_id": run_meta.get("run_id"),
        "apex_release_tag": run_meta.get("release_tag"),
        "apex_published_at": run_meta.get("published_at"),
    }
    return team, provenance


def validate_team_state_gameweek(provenance: dict[str, Any], gameweek: int) -> None:
    if provenance.get("mode") != "apex_private_strategy_snapshot":
        return
    target = _as_int(provenance.get("target_gameweek"), "private snapshot target_gameweek")
    if target != gameweek:
        raise ValueError(
            f"private Apex snapshot targets GW{target}, but Official FPL is_next is GW{gameweek}; refresh the private snapshot"
        )


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def resolve_team_source(args: argparse.Namespace) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if args.apex_strategy_snapshot is not None:
        snapshot = load_json_object(args.apex_strategy_snapshot, "Apex strategy snapshot")
        team, provenance = solver_team_from_apex_snapshot(snapshot)
        provenance["source_sha256"] = sha256_file(args.apex_strategy_snapshot)
        return team, provenance
    if args.team_file is not None:
        team = validate_solver_team(load_json_object(args.team_file, "team file"))
        meta = team.get("_meta") or {}
        entry_id = meta.get("entry_id") if isinstance(meta, dict) else None
        return team, {
            "mode": "exact_team_file",
            "entry_id": entry_id,
            "bank_tenths": int(team["transfers"]["bank"]),
            "free_transfers": int(team["transfers"]["limit"]),
            "source_sha256": sha256_file(args.team_file),
        }
    if args.entry_id is None:
        raise ValueError("one team source is required")
    if not args.public_state_is_current:
        raise ValueError(
            "public --entry-id state can hide transfers made since the last deadline; "
            "pass --public-state-is-current only after confirming the revealed squad is current"
        )
    if args.free_transfers is None or args.bank is None:
        raise ValueError("public --entry-id mode requires both --free-transfers and --bank")
    if not 0 <= args.free_transfers <= 5:
        raise ValueError("--free-transfers must be between 0 and 5")
    if args.bank < 0:
        raise ValueError("--bank must be non-negative")
    return None, {
        "mode": "public_entry_confirmed_current",
        "entry_id": args.entry_id,
        "bank_tenths": round(args.bank * 10),
        "free_transfers": args.free_transfers,
    }


def build_solve_command(
    *,
    solver: str,
    solver_csv: Path,
    solution_dir: Path,
    gameweek: int,
    posture: str,
    team_path: Path | None,
    entry_id: int | None,
    free_transfers: int | None,
    bank: float | None,
    plan_b: bool,
    no_hits: bool,
) -> list[str]:
    command = [solver, "solve"]
    if team_path is not None:
        command.extend(["--team", str(team_path)])
    elif entry_id is not None:
        command.extend(
            [
                "--entry-id",
                str(entry_id),
                "--free-transfers",
                str(free_transfers),
                "--bank",
                str(bank),
            ]
        )
    else:
        raise ValueError("solver source was not resolved")
    command.extend(
        [
            "--projections",
            str(solver_csv),
            "--gameweek",
            str(gameweek),
            "--horizon",
            "1",
            "--min-expected-minutes",
            str(GW1_MIN_EXPECTED_MINUTES),
            "--posture",
            posture,
            "--output-dir",
            str(solution_dir),
        ]
    )
    if plan_b:
        command.append("--plan-b")
    if no_hits:
        command.append("--no-hits")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-command £0 FPL strategy: open Dastan -> SmartPlay Solver"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--apex-strategy-snapshot", type=Path, help="local PRIVATE_MANAGER strategy_snapshot.json")
    source.add_argument("--team-file", type=Path, help="local SmartPlay solver team JSON")
    source.add_argument("--entry-id", type=int, help="public FPL entry ID; stale after hidden transfers")
    parser.add_argument("--public-state-is-current", action="store_true")
    parser.add_argument("--free-transfers", type=int)
    parser.add_argument("--bank", type=float, help="current bank in £m, e.g. 0.7")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--posture", choices=("neutral", "protect", "chase"), default="neutral")
    parser.add_argument("--plan-b", action="store_true")
    parser.add_argument("--no-hits", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--max-projection-age-hours", type=float, default=6.0)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    if args.horizon != 1:
        parser.error(
            "only --horizon 1 is certified in this free adapter. SmartPlay Solver supports "
            "multi-GW, but the open Dastan live adapter has not validated future-GW inputs; "
            "refusing to invent them."
        )
    if args.max_projection_age_hours <= 0:
        parser.error("--max-projection-age-hours must be positive")
    if args.entry_id is None and (args.free_transfers is not None or args.bank is not None):
        parser.error("exact private team modes take FT/bank from the supplied snapshot; do not override them")
    if args.entry_id is None and args.public_state_is_current:
        parser.error("--public-state-is-current only applies to --entry-id mode")

    try:
        exact_team, state_provenance = resolve_team_source(args)
    except ValueError as exc:
        parser.error(str(exc))

    entry_id = int(state_provenance.get("entry_id") or args.entry_id or DEFAULT_ENTRY_ID)
    root = Path(__file__).resolve().parent
    if not stack_ready(root):
        run(["bash", "bootstrap_v2.sh"], cwd=root)
    if not stack_ready(root):
        raise RuntimeError("pinned open-source stack failed post-bootstrap verification")

    bootstrap = fpl_bootstrap()
    gameweek = next_gameweek(bootstrap)
    try:
        validate_team_state_gameweek(state_provenance, gameweek)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    default_state_root = Path.home() / ".local" / "share" / "dastan-smartplay-free" / f"entry-{entry_id}" / f"gw{gameweek}"
    state_root = (args.output_dir or default_state_root).expanduser().resolve()
    repo_root = git_toplevel(root)
    if repo_root is not None and is_within(state_root, repo_root):
        parser.error(
            "strategy output may contain private squad/transfer information and must be outside the Git worktree"
        )
    state_root.mkdir(parents=True, exist_ok=True)
    projection_dir = state_root / "projections"
    solution_dir = state_root / "solution"
    projection_dir.mkdir(parents=True, exist_ok=True)

    acceptance = projection_dir / f"dastan_gw{gameweek}_acceptance.json"
    solver_csv = projection_dir / f"dastan_gw{gameweek}_solver.csv"
    fixtures_csv = projection_dir / f"dastan_gw{gameweek}_fixtures.csv"
    reference = root / f"smartplay_gw{gameweek}_spotcheck.csv"

    if args.force_refresh or not reusable_acceptance(
        acceptance, gameweek, args.max_projection_age_hours
    ):
        command = [
            str(root / ".venv" / "bin" / "python"),
            "live_gw_v2.py",
            "--gameweek",
            str(gameweek),
            "--dastan-repo",
            str(root / ".vendor" / "smartplayfpl-dastan"),
            "--smartplay-public-repo",
            str(root / ".vendor" / "smartplayfpl-public"),
            "--work-dir",
            str(Path.home() / ".cache" / "dastan-smartplay-free"),
            "--output-dir",
            str(projection_dir),
        ]
        if reference.exists():
            command.extend(["--reference", str(reference)])
        run(command, cwd=root)
    else:
        print(f"Reusing fresh GW{gameweek} Dastan projection snapshot: {acceptance}")

    if not acceptance.exists() or not solver_csv.exists() or not fixtures_csv.exists():
        raise RuntimeError(
            "projection run did not produce the required acceptance, aggregate solver and per-fixture files"
        )

    python = str(root / ".venv" / "bin" / "python")
    solver = str(root / ".venv" / "bin" / "smartplay-solver")
    run([python, "check_acceptance.py", str(acceptance), "--parity-mode", "auto"], cwd=root)
    run(
        [
            solver,
            "validate-projections",
            str(solver_csv),
            "--gameweek",
            str(gameweek),
            "--horizon",
            "1",
        ],
        cwd=root,
    )

    ephemeral_path: Path | None = None
    try:
        if exact_team is not None:
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="dastan-smartplay-team-",
                suffix=".json",
                delete=False,
            )
            with handle:
                json.dump(exact_team, handle, indent=2, sort_keys=True)
                handle.write("\n")
            ephemeral_path = Path(handle.name)
            try:
                os.chmod(ephemeral_path, 0o600)
            except OSError:
                pass

        solve_command = build_solve_command(
            solver=solver,
            solver_csv=solver_csv,
            solution_dir=solution_dir,
            gameweek=gameweek,
            posture=args.posture,
            team_path=ephemeral_path,
            entry_id=args.entry_id,
            free_transfers=args.free_transfers,
            bank=args.bank,
            plan_b=args.plan_b,
            no_hits=args.no_hits,
        )
        run(solve_command, cwd=root)
    finally:
        if ephemeral_path is not None:
            ephemeral_path.unlink(missing_ok=True)

    required_solution = [
        solution_dir / "summary.md",
        solution_dir / "picks.csv",
        solution_dir / "solution.json",
    ]
    missing = [str(path) for path in required_solution if not path.exists()]
    if missing:
        raise RuntimeError(f"solver did not produce required outputs: {missing}")

    state_manifest = dict(state_provenance)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entry_id": entry_id,
        "gameweek": gameweek,
        "horizon": 1,
        "posture": args.posture,
        "team_state": state_manifest,
        "inputs": {
            "projection_acceptance_sha256": sha256_file(acceptance),
            "projection_csv_sha256": sha256_file(solver_csv),
            "projection_fixtures_sha256": sha256_file(fixtures_csv),
        },
        "solution": {
            "summary_sha256": sha256_file(solution_dir / "summary.md"),
            "picks_sha256": sha256_file(solution_dir / "picks.csv"),
            "json_sha256": sha256_file(solution_dir / "solution.json"),
        },
        "upstream": {
            "dastan": DASTAN_SHA,
            "smartplay_solver": SOLVER_SHA,
            "smartplay_public_mapping": SMARTPLAY_PUBLIC_SHA,
        },
        "scientific_boundary": {
            "projection_horizon": 1,
            "solver_min_expected_minutes": GW1_MIN_EXPECTED_MINUTES,
            "hosted_smartplay_gw1_5_parity": "diagnostic_only",
        },
    }
    manifest_path = state_root / "strategy_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Strategy manifest: {manifest_path}")
    print(f"Human summary: {solution_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
