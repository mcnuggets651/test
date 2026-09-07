#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

DASTAN_SHA = "19376523afdec4836d0e6b5632c6773d0fe40c53"
SOLVER_SHA = "7ec56e944982020f8709db5d00b0b78821fb1f38"
SMARTPLAY_PUBLIC_SHA = "9b5bec6ae12541be24decd980e119af90617a868"


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


def fpl_bootstrap() -> dict:
    request = urllib.request.Request(
        "https://fantasy.premierleague.com/api/bootstrap-static/",
        headers={"User-Agent": "dastan-smartplay-free/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def next_gameweek(bootstrap: dict) -> int:
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
        and age <= dt.timedelta(hours=max_age_hours)
        and (payload.get("upstream") or {}).get("dastan") == DASTAN_SHA
        and (payload.get("upstream") or {}).get("smartplay_solver") == SOLVER_SHA
        and (payload.get("upstream") or {}).get("smartplay_public_mapping") == SMARTPLAY_PUBLIC_SHA
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-command £0 FPL strategy: open Dastan -> SmartPlay Solver"
    )
    parser.add_argument("--entry-id", type=int, required=True)
    parser.add_argument("--free-transfers", type=int, required=True, choices=range(0, 6))
    parser.add_argument("--bank", type=float, required=True, help="current bank in £m, e.g. 0.7")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--posture", choices=("neutral", "protect", "chase"), default="neutral")
    parser.add_argument("--plan-b", action="store_true")
    parser.add_argument("--no-hits", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--max-projection-age-hours", type=float, default=6.0)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.horizon != 1:
        parser.error(
            "only --horizon 1 is certified in this free adapter. SmartPlay Solver supports "
            "multi-GW, but the open Dastan live adapter has not yet validated future-GW inputs; "
            "refusing to invent them."
        )
    if args.bank < 0:
        parser.error("--bank must be non-negative")
    if args.max_projection_age_hours <= 0:
        parser.error("--max-projection-age-hours must be positive")

    root = Path(__file__).resolve().parent
    if not stack_ready(root):
        run(["bash", "bootstrap_v2.sh"], cwd=root)
    if not stack_ready(root):
        raise RuntimeError("pinned open-source stack failed post-bootstrap verification")

    bootstrap = fpl_bootstrap()
    gameweek = next_gameweek(bootstrap)
    state_root = args.output_dir or (root / "solver-output" / f"entry-{args.entry_id}" / f"gw{gameweek}")
    state_root.mkdir(parents=True, exist_ok=True)
    projection_dir = state_root / "projections"
    solution_dir = state_root / "solution"
    projection_dir.mkdir(parents=True, exist_ok=True)

    acceptance = projection_dir / f"dastan_gw{gameweek}_acceptance.json"
    solver_csv = projection_dir / f"dastan_gw{gameweek}_solver.csv"
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

    if not acceptance.exists() or not solver_csv.exists():
        raise RuntimeError("projection run did not produce the required acceptance and solver files")

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

    solve_command = [
        solver,
        "solve",
        "--entry-id",
        str(args.entry_id),
        "--projections",
        str(solver_csv),
        "--gameweek",
        str(gameweek),
        "--horizon",
        "1",
        "--free-transfers",
        str(args.free_transfers),
        "--bank",
        str(args.bank),
        "--posture",
        args.posture,
        "--output-dir",
        str(solution_dir),
    ]
    if args.plan_b:
        solve_command.append("--plan-b")
    if args.no_hits:
        solve_command.append("--no-hits")
    run(solve_command, cwd=root)

    manifest = {
        "schema": "dastan-smartplay-free-strategy-v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entry_id": args.entry_id,
        "gameweek": gameweek,
        "horizon": 1,
        "free_transfers": args.free_transfers,
        "bank_millions": args.bank,
        "posture": args.posture,
        "projection_acceptance": str(acceptance),
        "projection_csv": str(solver_csv),
        "solution": {
            "summary": str(solution_dir / "summary.md"),
            "picks": str(solution_dir / "picks.csv"),
            "json": str(solution_dir / "solution.json"),
        },
        "upstream": {
            "dastan": DASTAN_SHA,
            "smartplay_solver": SOLVER_SHA,
            "smartplay_public_mapping": SMARTPLAY_PUBLIC_SHA,
        },
    }
    manifest_path = state_root / "strategy_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Strategy manifest: {manifest_path}")
    print(f"Human summary: {solution_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
