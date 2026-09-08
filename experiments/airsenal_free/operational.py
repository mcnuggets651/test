#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import official_fpl
import owner_state as owner_adapter

PRIVATE_REPO_SLUG = "mcnuggets651/fpl"
PUBLIC_REPO_SLUG = "mcnuggets651/test"
CONTEXT_SCHEMA = "airsenal-chat-decision-context-v1"
RUN_MANIFEST_SCHEMA = "airsenal-chat-local-run-v1"
HORIZON_SCHEMA = "airsenal-chat-horizon-result-v1"
HORIZONS = (3, 5)
QUERY_FILES = (
    "tools/apex_strategy_query.py",
    "tools/apex_private_query.py",
    "tools/apex_private_query_entry.py",
)


class OperationalError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if private:
        path.chmod(0o600)


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise OperationalError(f"{label} must be a JSON object")
    return value


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise OperationalError(f"git {' '.join(args)} failed for {repo}: {detail[:500]}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def github_slug(remote: str) -> str | None:
    value = remote.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    for pattern in (
        r"^https?://github\.com/([^/]+/[^/]+)$",
        r"^ssh://git@github\.com/([^/]+/[^/]+)$",
        r"^git@github\.com:([^/]+/[^/]+)$",
    ):
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def experiment_repo(script_dir: Path) -> tuple[Path, str, dict[str, str]]:
    root_raw = git(script_dir, "rev-parse", "--show-toplevel", check=False)
    if root_raw.returncode != 0:
        raise OperationalError("experiment must run from a Git checkout of mcnuggets651/test")
    root = Path(root_raw.stdout.strip()).resolve()
    remote = git_text(root, "remote", "get-url", "origin")
    if (github_slug(remote) or "").lower() != PUBLIC_REPO_SLUG.lower():
        raise OperationalError(f"public experiment origin must be {PUBLIC_REPO_SLUG}")
    dirty = git_text(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        "experiments/airsenal_free",
        ".github/workflows/airsenal-chat-free.yml",
    )
    if dirty:
        raise OperationalError("public AIrsenal experiment files are locally modified/untracked; refusing unbound run")
    head = git_text(root, "rev-parse", "HEAD")
    tracked = git_text(root, "ls-files", "experiments/airsenal_free").splitlines()
    hashes = {rel: sha256_file(root / rel) for rel in tracked if (root / rel).is_file()}
    return root, head, hashes


def validate_private_repo(path: Path) -> dict[str, Any]:
    repo = path.expanduser().resolve()
    if not repo.is_dir():
        raise OperationalError(f"private repository does not exist: {repo}")
    top = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise OperationalError(f"--private-repo must point at Git toplevel: {top}")
    remote = git_text(repo, "remote", "get-url", "origin")
    if (github_slug(remote) or "").lower() != PRIVATE_REPO_SLUG.lower():
        raise OperationalError(f"private repository origin must be {PRIVATE_REPO_SLUG}")
    missing = [rel for rel in QUERY_FILES if not (repo / rel).is_file()]
    if missing:
        raise OperationalError(f"private query boundary missing files: {missing}")
    dirty = git_text(repo, "status", "--porcelain=v1", "--", *QUERY_FILES)
    if dirty:
        raise OperationalError("private query files have local modifications")
    return {
        "path": repo,
        "head": git_text(repo, "rev-parse", "HEAD"),
        "query_file_sha256": {rel: sha256_file(repo / rel) for rel in QUERY_FILES},
        "working_tree_status_before": git_text(repo, "status", "--porcelain=v1", "--untracked-files=all"),
    }


def resolve_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    gh = shutil.which("gh")
    if gh:
        result = subprocess.run([gh, "auth", "token"], text=True, capture_output=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise OperationalError("GitHub authentication unavailable; set GITHUB_TOKEN or authenticate gh")


def query_private_snapshot(repo_info: dict[str, Any], token: str, output: Path) -> None:
    repo = Path(repo_info["path"])
    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = PRIVATE_REPO_SLUG
    env["GITHUB_TOKEN"] = token
    command = [
        sys.executable,
        str(repo / "tools" / "apex_strategy_query.py"),
        "--run-id",
        "latest",
        "--top-n",
        "1",
        "--output",
        str(output),
    ]
    result = subprocess.run(
        command,
        cwd=repo / "tools",
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise OperationalError(f"OWNER_STATE_INVALID: authority-aware PRIVATE_MANAGER query failed ({result.returncode})")
    if not output.is_file():
        raise OperationalError("OWNER_STATE_INVALID: query succeeded without snapshot output")
    output.chmod(0o600)


def safe_runtime_env(home: Path, db_path: Path, entry_id: int) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "FPL_LOGIN",
        "FPL_PASSWORD",
        "FPL_LEAGUE_ID",
        "DISCORD_WEBHOOK",
        "AIRSENAL_DB_URI",
        "AIRSENAL_DB_USER",
        "AIRSENAL_DB_PASSWORD",
    ):
        env.pop(key, None)
    env["AIRSENAL_HOME"] = str(home)
    env["AIRSENAL_DB_FILE"] = str(db_path)
    env["FPL_TEAM_ID"] = str(entry_id)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def command_path(name: str) -> Path:
    # sys.executable may be a symlink on macOS. Resolving it escapes the
    # active venv into the Homebrew framework bin and hides console scripts
    # installed in the venv. sys.prefix is the authoritative active env root.
    candidates = (
        Path(sys.prefix) / "bin" / name,
        Path(sys.executable).parent / name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise OperationalError(f"UPSTREAM_PIN_INVALID: required installed command not found: {name}")


def run_logged(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path, label: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=cwd, env=env, text=True, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise OperationalError(f"{label} failed with exit code {result.returncode}; see local log {log_path}")


def verify_runtime_pin(home: Path, pins: dict[str, Any]) -> dict[str, Any]:
    upstream = home / "upstream" / "AIrsenal"
    if not (upstream / ".git").is_dir():
        raise OperationalError("UPSTREAM_PIN_INVALID: bootstrap has not materialised upstream AIrsenal")
    actual_sha = git_text(upstream, "rev-parse", "HEAD")
    expected_sha = str(pins["upstream_sha"])
    if actual_sha != expected_sha:
        raise OperationalError(f"UPSTREAM_PIN_INVALID: expected {expected_sha}, got {actual_sha}")
    if git_text(upstream, "status", "--porcelain=v1"):
        raise OperationalError("UPSTREAM_PIN_INVALID: upstream checkout is dirty")
    license_blob = git_text(upstream, "hash-object", "LICENSE")
    lock_blob = git_text(upstream, "hash-object", "uv.lock")
    if license_blob != pins.get("upstream_license_blob_sha"):
        raise OperationalError("UPSTREAM_PIN_INVALID: LICENSE blob does not match pin")
    if lock_blob != pins.get("upstream_uv_lock_blob_sha"):
        raise OperationalError("UPSTREAM_PIN_INVALID: uv.lock blob does not match pin")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import airsenal,sys; print(getattr(airsenal,'__version__','')); print(sys.version.split()[0]); print(airsenal.__file__)",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        raise OperationalError("UPSTREAM_PIN_INVALID: installed AIrsenal cannot be imported")
    lines = probe.stdout.strip().splitlines()
    version = lines[0] if lines else ""
    python_version = lines[1] if len(lines) > 1 else ""
    module_path = lines[2] if len(lines) > 2 else ""
    if version != str(pins.get("upstream_project_version")):
        raise OperationalError(
            f"UPSTREAM_PIN_INVALID: installed AIrsenal version {version!r} != {pins.get('upstream_project_version')!r}"
        )
    expected_python = str(pins.get("python_version", pins.get("python_minor", "3.12")))
    if pins.get("python_version"):
        if python_version != expected_python:
            raise OperationalError(f"UPSTREAM_PIN_INVALID: Python {python_version!r} != pin {expected_python!r}")
    elif not python_version.startswith(expected_python + "."):
        raise OperationalError(f"UPSTREAM_PIN_INVALID: Python {python_version!r} != pin {expected_python!r}")
    return {
        "upstream_checkout": str(upstream),
        "upstream_sha": actual_sha,
        "license_blob_sha": license_blob,
        "uv_lock_blob_sha": lock_blob,
        "airsenal_version": version,
        "python_version": python_version,
        "airsenal_module_path": module_path,
    }


def build_or_update_base_db(home: Path, entry_id: int, target_gw: int, script_dir: Path) -> tuple[Path, str]:
    base_home = home / "base"
    base_db = base_home / "data.db"
    base_home.mkdir(parents=True, exist_ok=True)
    env = safe_runtime_env(base_home, base_db, entry_id)
    if not base_db.is_file():
        run_logged(
            [str(command_path("airsenal_setup_initial_db"))],
            cwd=script_dir,
            env=env,
            log_path=base_home / "setup_initial_db.log",
            label="DATA_BOOTSTRAP_FAILED: AIrsenal initial DB setup",
        )
    run_logged(
        [str(command_path("airsenal_update_db"))],
        cwd=script_dir,
        env=env,
        log_path=base_home / "update_db.log",
        label="DATA_BOOTSTRAP_FAILED: AIrsenal DB update",
    )
    if not base_db.is_file() or base_db.stat().st_size == 0:
        raise OperationalError("DATA_BOOTSTRAP_FAILED: AIrsenal base DB missing/empty after update")
    probe = subprocess.run(
        [sys.executable, "-c", "from airsenal.framework.utils import NEXT_GAMEWEEK; print(NEXT_GAMEWEEK)"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        raise OperationalError("DATA_BOOTSTRAP_FAILED: could not resolve AIrsenal NEXT_GAMEWEEK from isolated DB")
    try:
        airsenal_next_gw = int(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise OperationalError("DATA_BOOTSTRAP_FAILED: AIrsenal NEXT_GAMEWEEK probe invalid") from exc
    if airsenal_next_gw != int(target_gw):
        raise OperationalError(
            f"DATA_BOOTSTRAP_FAILED: AIrsenal next GW{airsenal_next_gw} != verified owner/Official GW{target_gw}"
        )
    return base_db, sha256_file(base_db)


def validate_horizon_result(
    path: Path,
    *,
    horizon: int,
    entry_id: int,
    target_gw: int,
    base_db_sha256: str,
) -> dict[str, Any]:
    result = load_object(path, f"H{horizon} result")
    if result.get("schema") != HORIZON_SCHEMA:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} result schema mismatch")
    if int(result.get("horizon") or 0) != horizon:
        raise OperationalError(f"HORIZON_FAILED: expected H{horizon}, got {result.get('horizon')}")
    if int(result.get("entry_id") or 0) != entry_id or int(result.get("target_gameweek") or 0) != target_gw:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} owner scope mismatch")
    expected_gws = list(range(target_gw, target_gw + horizon))
    if result.get("gameweeks") != expected_gws:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} gameweek range mismatch")
    db = result.get("db") or {}
    if db.get("pre_prediction_sha256") != base_db_sha256:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} did not start from frozen base DB")
    strategy = (result.get("optimizer") or {}).get("strategy") or {}
    by_gw = strategy.get("expected_points_by_gameweek") or {}
    if set(by_gw) != {str(gw) for gw in expected_gws}:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} missing expected points by Gameweek")
    price_rows = (result.get("optimizer") or {}).get("starting_price_reconciliation") or []
    if len(price_rows) != 15 or not all(row.get("match") is True for row in price_rows if isinstance(row, dict)):
        raise OperationalError(f"PRICE_RECONCILIATION_FAILED: H{horizon} starting price reconciliation invalid")
    return result


def run_horizon(
    *,
    script_dir: Path,
    run_dir: Path,
    base_db: Path,
    base_db_sha256: str,
    owner_path: Path,
    owner: dict[str, Any],
    horizon: int,
    scenario_path: Path | None,
) -> dict[str, Any]:
    hdir = run_dir / f"h{horizon}"
    hdir.mkdir(parents=True, exist_ok=False)
    db_copy = hdir / "data.db"
    shutil.copy2(base_db, db_copy)
    if sha256_file(db_copy) != base_db_sha256:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} DB copy hash mismatch")
    home = hdir / "home"
    home.mkdir()
    env = safe_runtime_env(home, db_copy, int(owner["entry_id"]))

    prediction_path = hdir / "prediction_stage.json"
    prediction_command = [
        sys.executable,
        str(script_dir / "prediction_worker.py"),
        "--owner-state",
        str(owner_path),
        "--horizon",
        str(horizon),
        "--output",
        str(prediction_path),
    ]
    run_logged(
        prediction_command,
        cwd=script_dir,
        env=env,
        log_path=hdir / "prediction.log",
        label=f"HORIZON_FAILED: H{horizon} AIrsenal prediction stage",
    )
    prediction = load_object(prediction_path, f"H{horizon} prediction stage")
    expected_gws = list(range(int(owner["target_gameweek"]), int(owner["target_gameweek"]) + horizon))
    if prediction.get("schema") != "airsenal-chat-prediction-stage-v1":
        raise OperationalError(f"HORIZON_FAILED: H{horizon} prediction-stage schema mismatch")
    if int(prediction.get("horizon") or 0) != horizon or prediction.get("gameweeks") != expected_gws:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} prediction-stage scope mismatch")
    if prediction.get("pre_prediction_db_sha256") != base_db_sha256:
        raise OperationalError(f"HORIZON_FAILED: H{horizon} prediction did not start from frozen base DB")
    if prediction.get("post_prediction_db_sha256") != sha256_file(db_copy):
        raise OperationalError(f"HORIZON_FAILED: H{horizon} prediction DB hash mismatch")

    result_path = hdir / "result.json"
    command = [
        sys.executable,
        str(script_dir / "horizon_worker.py"),
        "--owner-state",
        str(owner_path),
        "--horizon",
        str(horizon),
        "--prediction-manifest",
        str(prediction_path),
        "--output",
        str(result_path),
    ]
    if scenario_path is not None:
        command += ["--scenario", str(scenario_path)]
    run_logged(
        command,
        cwd=script_dir,
        env=env,
        log_path=hdir / "worker.log",
        label=f"HORIZON_FAILED: H{horizon} AIrsenal optimization stage",
    )
    return validate_horizon_result(
        result_path,
        horizon=horizon,
        entry_id=int(owner["entry_id"]),
        target_gw=int(owner["target_gameweek"]),
        base_db_sha256=base_db_sha256,
    )


def build_context(
    *,
    run_timestamp: str,
    owner: dict[str, Any],
    official_manifest: dict[str, Any],
    repo_info: dict[str, Any],
    private_info: dict[str, Any],
    runtime_pin: dict[str, Any],
    pins: dict[str, Any],
    base_db_sha256: str,
    h3: dict[str, Any],
    h5: dict[str, Any],
    run_dir: Path,
    scenario_path: Path | None,
) -> dict[str, Any]:
    files = {
        "owner_state.json": sha256_file(run_dir / "owner_state.json"),
        "official_fpl_provenance.json": sha256_file(run_dir / "official_fpl_provenance.json"),
        "h3/result.json": sha256_file(run_dir / "h3" / "result.json"),
        "h5/result.json": sha256_file(run_dir / "h5" / "result.json"),
    }
    scenario_sha = sha256_file(scenario_path) if scenario_path is not None else None
    return {
        "schema": CONTEXT_SCHEMA,
        "run_timestamp": run_timestamp,
        "entry_id": int(owner["entry_id"]),
        "target_gameweek": int(owner["target_gameweek"]),
        "owner_state": {
            "bank_tenths": int(owner["bank_tenths"]),
            "free_transfers": int(owner["free_transfers"]),
            "active_chip": owner.get("active_chip"),
            "squad": owner["squad"],
        },
        "private_authority": owner["private_authority"],
        "provenance": {
            "private_repository": PRIVATE_REPO_SLUG,
            "private_query_code_head": private_info["head"],
            "private_query_file_sha256": private_info["query_file_sha256"],
            "official_fpl": official_manifest,
            "airsenal_upstream_repository": pins["upstream_repository"],
            "airsenal_upstream_sha": pins["upstream_sha"],
            "airsenal_upstream_license": pins["upstream_license"],
            "airsenal_uv_lock_blob_sha": pins["upstream_uv_lock_blob_sha"],
            "airsenal_runtime": runtime_pin,
            "experiment_repository": PUBLIC_REPO_SLUG,
            "experiment_code_sha": repo_info["head"],
            "experiment_file_sha256": repo_info["file_sha256"],
            "frozen_base_db_sha256": base_db_sha256,
            "scenario_sha256": scenario_sha,
        },
        "airsenal_result": {"h3": h3, "h5": h5},
        "model_limitations": {
            "minutes": "Recent-minutes samples are averaged inside point calculation; pinned upstream does not expose a scalar future xMin probability surface.",
            "injuries": "AIrsenal absence/status logic is model evidence; late manager quotes/training/cup context require separately timestamped external evidence.",
            "fixture_model": "Extended Dixon-Coles team model plus player involvement model; not a comprehensive tactical event-level model.",
            "bonus_saves_cards_defcon": "Pinned run includes empirical bonus, goalkeeper save, card and defensive-contribution components.",
            "new_players": "Sparse-history/new players rely on upstream priors/fallbacks and may be less stable.",
            "future_prices": "Future FPL price changes are not forecast in football expected points.",
            "future_transfer_plans": "Planning evidence only; upstream assumes re-optimisation in later Gameweeks.",
            "named_player_constraints": "Not exposed by this integration; no fake keep/force/buy counterfactuals are produced.",
            "chips": "Only explicit supported chip constraints or an already-active target-GW chip are represented; unused chip inventory is not guessed.",
        },
        "interpretation_contract": {
            "airsenal_xp_immutable_model_evidence": True,
            "airsenal_optimizer_output_immutable_evidence": True,
            "chatgpt_may_challenge_but_not_rewrite": True,
            "external_evidence_must_be_separate_sourced_and_timestamped": True,
            "unsolved_counterfactual_numerical_edges_forbidden": True,
            "layers": ["airsenal_result", "external_evidence", "ai_interpretation", "final_recommendation"],
            "fpl_account_execution_permitted": False,
        },
        "external_evidence": [],
        "ai_interpretation": None,
        "final_recommendation": None,
        "integrity": {
            "files": files,
            "same_owner_snapshot_for_h3_h5": True,
            "same_pre_prediction_db_sha256_for_h3_h5": h3["db"]["pre_prediction_sha256"]
            == h5["db"]["pre_prediction_sha256"]
            == base_db_sha256,
        },
        "privacy": {
            "classification": "PRIVATE_MANAGER_LOCAL_ONLY",
            "safe_for_public_artifact_upload": False,
            "source_fields_whitelisted": True,
            "raw_private_snapshot_retained": False,
            "reusable_fpl_credentials_present": False,
        },
    }


def create_run_dir(
    home: Path,
    owner: dict[str, Any],
    base_db_sha256: str,
    now: dt.datetime | None = None,
) -> tuple[Path, str]:
    stamp_dt = now or dt.datetime.now(dt.timezone.utc)
    stamp = stamp_dt.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_sha = str((owner.get("private_authority") or {}).get("raw_snapshot_sha256") or "")
    run_id = f"{stamp}-{raw_sha[:10]}-{base_db_sha256[:10]}"
    run_dir = (
        home
        / "runs"
        / f"entry-{int(owner['entry_id'])}"
        / f"gw{int(owner['target_gameweek'])}"
        / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    run_dir.chmod(0o700)
    return run_dir, run_id


def attempt_chat_publish(*, run_dir: Path, private_repo: Path, no_chat_publish: bool) -> dict[str, Any]:
    if no_chat_publish:
        return {"status": "disabled", "reason": "--no-chat-publish"}
    try:
        import publish_chat_bridge
        import verify_chat_bridge

        result = publish_chat_bridge.publish(run_dir=run_dir, private_repo=private_repo)
        result["retrieval_verification"] = verify_chat_bridge.verify(
            private_repo=private_repo,
            entry_id=int(result["entry_id"]),
        )
        return result
    except Exception as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
            "calculation_still_valid": True,
        }


def run_experiment(
    *,
    script_dir: Path,
    private_repo: Path,
    home: Path,
    scenario_path: Path | None,
    no_chat_publish: bool,
    token: str | None = None,
) -> dict[str, Any]:
    script_dir = script_dir.resolve()
    public_root, public_head, public_hashes = experiment_repo(script_dir)
    private_info = validate_private_repo(private_repo)
    home = home.expanduser().resolve()
    if is_within(home, public_root) or is_within(home, Path(private_info["path"])):
        raise OperationalError("operational home must be outside public/private Git worktrees")
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    pins = load_object(script_dir / "pins.json", "pins")
    runtime_pin = verify_runtime_pin(home, pins)
    token = token or resolve_github_token()

    with tempfile.TemporaryDirectory(prefix="airsenal-chat-owner-") as temp_raw:
        temp = Path(temp_raw)
        temp.chmod(0o700)
        raw_snapshot = temp / "strategy_snapshot.json"
        query_private_snapshot(private_info, token, raw_snapshot)

        official_stage = temp / "official"
        acquired = official_fpl.acquire(official_stage)
        owner_stage = temp / "owner_state.json"
        owner = owner_adapter.write_owner_state(raw_snapshot, acquired["bootstrap_path"], owner_stage)
        if int(owner["entry_id"]) != int(pins["private_entry_id"]):
            raise OperationalError("OWNER_STATE_INVALID: owner entry does not match experiment pin")

        base_db, base_db_sha256 = build_or_update_base_db(
            home,
            int(owner["entry_id"]),
            int(owner["target_gameweek"]),
            script_dir,
        )
        run_dir, run_id = create_run_dir(home, owner, base_db_sha256)
        owner_path = run_dir / "owner_state.json"
        official_manifest_path = run_dir / "official_fpl_provenance.json"
        shutil.copy2(owner_stage, owner_path)
        shutil.copy2(acquired["manifest_path"], official_manifest_path)
        owner_path.chmod(0o600)

        if scenario_path is not None:
            scenario_path = scenario_path.expanduser().resolve()
            if not scenario_path.is_file():
                raise OperationalError(f"scenario file not found: {scenario_path}")
            scenario_copy = run_dir / "scenario.json"
            shutil.copy2(scenario_path, scenario_copy)
            scenario_path = scenario_copy

        h3 = run_horizon(
            script_dir=script_dir,
            run_dir=run_dir,
            base_db=base_db,
            base_db_sha256=base_db_sha256,
            owner_path=owner_path,
            owner=owner,
            horizon=3,
            scenario_path=scenario_path,
        )
        h5 = run_horizon(
            script_dir=script_dir,
            run_dir=run_dir,
            base_db=base_db,
            base_db_sha256=base_db_sha256,
            owner_path=owner_path,
            owner=owner,
            horizon=5,
            scenario_path=scenario_path,
        )
        run_timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        context = build_context(
            run_timestamp=run_timestamp,
            owner=owner,
            official_manifest=load_object(official_manifest_path, "Official FPL provenance"),
            repo_info={"head": public_head, "file_sha256": public_hashes},
            private_info=private_info,
            runtime_pin=runtime_pin,
            pins=pins,
            base_db_sha256=base_db_sha256,
            h3=h3,
            h5=h5,
            run_dir=run_dir,
            scenario_path=scenario_path,
        )
        context_path = run_dir / "decision_context.json"
        write_json(context_path, context, private=True)
        context_sha = sha256_file(context_path)
        (run_dir / "decision_context.sha256").write_text(
            f"{context_sha}  decision_context.json\n",
            encoding="utf-8",
        )

        manifest = {
            "schema": RUN_MANIFEST_SCHEMA,
            "status": "ready",
            "run_id": run_id,
            "run_timestamp": run_timestamp,
            "entry_id": int(owner["entry_id"]),
            "target_gameweek": int(owner["target_gameweek"]),
            "run_dir": str(run_dir),
            "decision_context_sha256": context_sha,
            "calculation": {"status": "ready", "horizons": [3, 5], "base_db_sha256": base_db_sha256},
            "chat_transport": {"status": "disabled" if no_chat_publish else "pending"},
            "private_working_tree_unchanged": None,
        }
        manifest_path = run_dir / "run_manifest.json"
        write_json(manifest_path, manifest, private=True)

        publish_result = attempt_chat_publish(
            run_dir=run_dir,
            private_repo=Path(private_info["path"]),
            no_chat_publish=no_chat_publish,
        )

        after_status = git_text(
            Path(private_info["path"]),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        manifest["private_working_tree_unchanged"] = (
            after_status == private_info["working_tree_status_before"]
        )
        if not manifest["private_working_tree_unchanged"]:
            raise OperationalError("normal private working tree changed during AIrsenal run")
        manifest["chat_transport"] = publish_result
        write_json(manifest_path, manifest, private=True)
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated £0 H3/H5 AIrsenal owner strategy and private chat bridge"
    )
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ.get("AIRSENAL_CHAT_HOME", "~/.local/share/airsenal-chat")).expanduser(),
    )
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--no-chat-publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = run_experiment(
            script_dir=Path(__file__).resolve().parent,
            private_repo=args.private_repo,
            home=args.home,
            scenario_path=args.scenario,
            no_chat_publish=args.no_chat_publish,
        )
    except (
        OperationalError,
        owner_adapter.OwnerStateError,
        official_fpl.OfficialFPLError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"AIrsenal H3/H5 calculation: {manifest['calculation']['status']}")
    print(f"Private run: {manifest['run_dir']}")
    transport = manifest.get("chat_transport") or {}
    if transport.get("status") == "ready":
        print(
            f"Chat bridge synced: {transport['repository']}@{transport['branch']}:{transport['pointer_path']}"
        )
        print("Fresh-chat phrase: Check my latest AIrsenal strategy.")
    elif transport.get("status") == "disabled":
        print("Chat bridge disabled for this run (--no-chat-publish).")
    else:
        print(
            f"WARNING: CHAT_PUBLISH_FAILED: {transport.get('message', transport)}",
            file=sys.stderr,
        )
        print(
            "The local H3/H5 AIrsenal calculation remains scientifically valid.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
