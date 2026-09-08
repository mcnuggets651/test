#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import official_fpl
import operational as op
import owner_state as owner_adapter
import publish_chat_bridge
import publish_staged_bridge
import verify_chat_bridge
import verify_staged_bridge


def _failure_class(stage: str, exc: BaseException) -> str:
    text = str(exc).upper()
    for marker in (
        "OWNER_STATE_INVALID",
        "DATA_BOOTSTRAP_FAILED",
        "UPSTREAM_PIN_INVALID",
        "PRICE_RECONCILIATION_FAILED",
        "HORIZON_FAILED",
        "CHAT_PUBLISH_FAILED",
    ):
        if marker in text:
            return marker
    return f"{stage.upper()}_FAILED"


def _validate_forecast(path: Path, *, entry_id: int, target_gw: int, base_db_sha256: str) -> dict[str, Any]:
    forecast = op.load_object(path, "forecast")
    if forecast.get("schema") != "airsenal-chat-prediction-stage-v1":
        raise op.OperationalError("FORECAST_FAILED: forecast schema mismatch")
    if int(forecast.get("horizon") or 0) != 5:
        raise op.OperationalError("FORECAST_FAILED: morning forecast must cover H5")
    if int(forecast.get("entry_id") or 0) != entry_id or int(forecast.get("target_gameweek") or 0) != target_gw:
        raise op.OperationalError("FORECAST_FAILED: forecast owner scope mismatch")
    expected = list(range(target_gw, target_gw + 5))
    if forecast.get("gameweeks") != expected:
        raise op.OperationalError("FORECAST_FAILED: forecast gameweek range mismatch")
    if forecast.get("pre_prediction_db_sha256") != base_db_sha256:
        raise op.OperationalError("FORECAST_FAILED: forecast did not start from frozen base DB")
    players = forecast.get("players") or []
    if not isinstance(players, list) or len(players) < 100:
        raise op.OperationalError("FORECAST_FAILED: forecast player coverage unexpectedly small")
    ids = [int(row.get("element_id") or 0) for row in players if isinstance(row, dict)]
    if len(ids) != len(set(ids)) or any(pid <= 0 for pid in ids):
        raise op.OperationalError("FORECAST_FAILED: forecast player identity invalid")
    return forecast


def run_daily_producer(*, script_dir: Path, private_repo: Path, home: Path, scenario_path: Path | None, token: str | None = None) -> dict[str, Any]:
    script_dir = script_dir.resolve()
    public_root, public_head, public_hashes = op.experiment_repo(script_dir)
    private_info = op.validate_private_repo(private_repo)
    home = home.expanduser().resolve()
    if op.is_within(home, public_root) or op.is_within(home, Path(private_info["path"])):
        raise op.OperationalError("producer home must be outside public/private Git worktrees")
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    pins = op.load_object(script_dir / "pins.json", "pins")
    runtime_pin = op.verify_runtime_pin(home, pins)
    token = token or op.resolve_github_token()

    with tempfile.TemporaryDirectory(prefix="airsenal-producer-owner-") as temp_raw:
        temp = Path(temp_raw)
        temp.chmod(0o700)
        raw_snapshot = temp / "strategy_snapshot.json"
        op.query_private_snapshot(private_info, token, raw_snapshot)
        official_stage = temp / "official"
        acquired = official_fpl.acquire(official_stage)
        owner_stage = temp / "owner_state.json"
        owner = owner_adapter.write_owner_state(raw_snapshot, acquired["bootstrap_path"], owner_stage)
        if int(owner["entry_id"]) != int(pins["private_entry_id"]):
            raise op.OperationalError("OWNER_STATE_INVALID: owner entry does not match experiment pin")

        entry_id = int(owner["entry_id"])
        target_gw = int(owner["target_gameweek"])
        base_db, base_db_sha256 = op.build_or_update_base_db(home, entry_id, target_gw, script_dir)
        run_dir, producer_run_id = op.create_run_dir(home, owner, base_db_sha256)
        owner_path = run_dir / "owner_state.json"
        official_path = run_dir / "official_fpl_provenance.json"
        shutil.copy2(owner_stage, owner_path)
        shutil.copy2(acquired["manifest_path"], official_path)
        owner_path.chmod(0o600)

        if scenario_path is not None:
            scenario_path = scenario_path.expanduser().resolve()
            if not scenario_path.is_file():
                raise op.OperationalError(f"scenario file not found: {scenario_path}")
            scenario_copy = run_dir / "scenario.json"
            shutil.copy2(scenario_path, scenario_copy)
            scenario_path = scenario_copy

        publish_staged_bridge.initialize(
            private_repo=Path(private_info["path"]),
            entry_id=entry_id,
            target_gw=target_gw,
            producer_run_id=producer_run_id,
            owner_state_path=owner_path,
            official_path=official_path,
            experiment_sha=public_head,
            upstream_sha=str(pins["upstream_sha"]),
            scenario_path=scenario_path,
        )

        # Forecast is intentionally produced and published before either optimizer.
        forecast_dir = run_dir / "forecast"
        forecast_dir.mkdir()
        forecast_db = forecast_dir / "data.db"
        shutil.copy2(base_db, forecast_db)
        if op.sha256_file(forecast_db) != base_db_sha256:
            raise op.OperationalError("FORECAST_FAILED: forecast DB copy hash mismatch")
        forecast_home = forecast_dir / "home"
        forecast_home.mkdir()
        forecast_env = op.safe_runtime_env(forecast_home, forecast_db, entry_id)
        forecast_path = forecast_dir / "forecast.json"
        try:
            op.run_logged(
                [
                    sys.executable,
                    str(script_dir / "prediction_worker.py"),
                    "--owner-state",
                    str(owner_path),
                    "--horizon",
                    "5",
                    "--output",
                    str(forecast_path),
                ],
                cwd=script_dir,
                env=forecast_env,
                log_path=forecast_dir / "prediction.log",
                label="FORECAST_FAILED: AIrsenal H5 forecast stage",
            )
            forecast = _validate_forecast(
                forecast_path,
                entry_id=entry_id,
                target_gw=target_gw,
                base_db_sha256=base_db_sha256,
            )
            forecast_publish = publish_staged_bridge.publish_stage(
                private_repo=Path(private_info["path"]),
                stage="forecast",
                artifact_path=forecast_path,
                owner_state_path=owner_path,
                official_path=official_path,
                producer_run_id=producer_run_id,
                experiment_sha=public_head,
                upstream_sha=str(pins["upstream_sha"]),
                scenario_path=scenario_path,
            )
        except Exception as exc:
            try:
                publish_staged_bridge.mark_failed(
                    private_repo=Path(private_info["path"]),
                    entry_id=entry_id,
                    producer_run_id=producer_run_id,
                    stage="forecast",
                    failure_class=_failure_class("forecast", exc),
                )
            finally:
                raise

        try:
            h3 = op.run_horizon(
                script_dir=script_dir,
                run_dir=run_dir,
                base_db=base_db,
                base_db_sha256=base_db_sha256,
                owner_path=owner_path,
                owner=owner,
                horizon=3,
                scenario_path=scenario_path,
            )
            h3_publish = publish_staged_bridge.publish_stage(
                private_repo=Path(private_info["path"]),
                stage="h3",
                artifact_path=run_dir / "h3" / "result.json",
                owner_state_path=owner_path,
                official_path=official_path,
                producer_run_id=producer_run_id,
                experiment_sha=public_head,
                upstream_sha=str(pins["upstream_sha"]),
                scenario_path=scenario_path,
            )
        except Exception as exc:
            try:
                publish_staged_bridge.mark_failed(
                    private_repo=Path(private_info["path"]),
                    entry_id=entry_id,
                    producer_run_id=producer_run_id,
                    stage="h3",
                    failure_class=_failure_class("h3", exc),
                )
            finally:
                raise

        try:
            h5 = op.run_horizon(
                script_dir=script_dir,
                run_dir=run_dir,
                base_db=base_db,
                base_db_sha256=base_db_sha256,
                owner_path=owner_path,
                owner=owner,
                horizon=5,
                scenario_path=scenario_path,
            )
            h5_publish = publish_staged_bridge.publish_stage(
                private_repo=Path(private_info["path"]),
                stage="h5",
                artifact_path=run_dir / "h5" / "result.json",
                owner_state_path=owner_path,
                official_path=official_path,
                producer_run_id=producer_run_id,
                experiment_sha=public_head,
                upstream_sha=str(pins["upstream_sha"]),
                scenario_path=scenario_path,
            )
        except Exception as exc:
            try:
                publish_staged_bridge.mark_failed(
                    private_repo=Path(private_info["path"]),
                    entry_id=entry_id,
                    producer_run_id=producer_run_id,
                    stage="h5",
                    failure_class=_failure_class("h5", exc),
                )
            finally:
                raise

        run_timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        context = op.build_context(
            run_timestamp=run_timestamp,
            owner=owner,
            official_manifest=op.load_object(official_path, "Official FPL provenance"),
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
        op.write_json(context_path, context, private=True)
        context_sha = op.sha256_file(context_path)
        (run_dir / "decision_context.sha256").write_text(
            f"{context_sha}  decision_context.json\n", encoding="utf-8"
        )
        manifest = {
            "schema": op.RUN_MANIFEST_SCHEMA,
            "status": "ready",
            "run_id": producer_run_id,
            "run_timestamp": run_timestamp,
            "entry_id": entry_id,
            "target_gameweek": target_gw,
            "run_dir": str(run_dir),
            "decision_context_sha256": context_sha,
            "calculation": {"status": "ready", "horizons": [3, 5], "base_db_sha256": base_db_sha256},
            "staged_transport": {
                "forecast": forecast_publish,
                "h3": h3_publish,
                "h5": h5_publish,
            },
            "chat_transport": {"status": "pending"},
            "private_working_tree_unchanged": None,
        }
        run_manifest_path = run_dir / "run_manifest.json"
        op.write_json(run_manifest_path, manifest, private=True)

        legacy_result = publish_chat_bridge.publish(
            run_dir=run_dir,
            private_repo=Path(private_info["path"]),
        )
        legacy_verify = verify_chat_bridge.verify(
            private_repo=Path(private_info["path"]), entry_id=entry_id
        )
        combined_marker = publish_staged_bridge.mark_combined_ready(
            private_repo=Path(private_info["path"]),
            entry_id=entry_id,
            producer_run_id=producer_run_id,
            legacy_result=legacy_result,
        )
        staged_verify = verify_staged_bridge.verify(
            private_repo=Path(private_info["path"]), entry_id=entry_id
        )
        if staged_verify["status"] != "ready" or not staged_verify["combined"]["verified"]:
            raise op.OperationalError("CHAT_PUBLISH_FAILED: staged producer verification did not reach ready")

        after_status = op.git_text(
            Path(private_info["path"]), "status", "--porcelain=v1", "--untracked-files=all"
        )
        manifest["private_working_tree_unchanged"] = after_status == private_info["working_tree_status_before"]
        if not manifest["private_working_tree_unchanged"]:
            raise op.OperationalError("normal private working tree changed during AIrsenal producer run")
        manifest["chat_transport"] = {
            "status": "ready",
            "legacy": legacy_result,
            "legacy_verification": legacy_verify,
            "staged_manifest": combined_marker,
            "staged_verification": staged_verify,
        }
        op.write_json(run_manifest_path, manifest, private=True)
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precompute and stage-publish AIrsenal forecast/H3/H5 evidence for conversational use")
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ.get("AIRSENAL_CHAT_HOME", "~/.local/share/airsenal-chat")).expanduser(),
    )
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_daily_producer(
            script_dir=Path(__file__).resolve().parent,
            private_repo=args.private_repo,
            home=args.home,
            scenario_path=args.scenario,
        )
    except (
        op.OperationalError,
        owner_adapter.OwnerStateError,
        official_fpl.OfficialFPLError,
        publish_staged_bridge.StagedPublishError,
        publish_chat_bridge.PublishError,
        verify_staged_bridge.VerifyStagedError,
        verify_chat_bridge.VerifyError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    transport = result["chat_transport"]
    print("AIrsenal staged producer: ready")
    print(f"Private run: {result['run_dir']}")
    print(f"Staged manifest: {transport['staged_manifest']['manifest_path']}")
    print(f"Legacy combined pointer: {transport['legacy']['pointer_path']}")
    print("Fresh-chat phrase: Check my latest AIrsenal strategy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
