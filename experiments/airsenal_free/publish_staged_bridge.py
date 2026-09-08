#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import publish_chat_bridge as legacy

PRIVATE_REPO_SLUG = legacy.PRIVATE_REPO_SLUG
RESULTS_BRANCH = legacy.RESULTS_BRANCH
STAGED_POINTER_SCHEMA = "airsenal-staged-pointer-v1"
PRODUCER_MANIFEST_SCHEMA = "airsenal-producer-manifest-v1"
STAGE_MANIFEST_SCHEMA = "airsenal-stage-run-v1"
STAGES = ("forecast", "h3", "h5")


class StagedPublishError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StagedPublishError(f"expected object: {path}")
    return value


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def manifest_rel(entry_id: int) -> Path:
    return Path("airsenal") / "latest" / f"entry-{entry_id}" / "manifest.json"


def pointer_rel(entry_id: int, stage: str) -> Path:
    return Path("airsenal") / "latest" / f"entry-{entry_id}" / f"{stage}.json"


def _base_manifest(*, entry_id: int, target_gw: int, producer_run_id: str, owner_sha: str, official_sha: str, experiment_sha: str, upstream_sha: str, scenario_sha: str | None, started_at: str) -> dict[str, Any]:
    return {
        "schema": PRODUCER_MANIFEST_SCHEMA,
        "status": "running",
        "repository": PRIVATE_REPO_SLUG,
        "branch": RESULTS_BRANCH,
        "entry_id": entry_id,
        "target_gameweek": target_gw,
        "producer_run_id": producer_run_id,
        "started_at": started_at,
        "updated_at": started_at,
        "owner_state_sha256": owner_sha,
        "official_fpl_provenance_sha256": official_sha,
        "public_experiment_sha": experiment_sha,
        "upstream_airsenal_sha": upstream_sha,
        "scenario_sha256": scenario_sha,
        "freshness": {
            "max_age_hours": 24,
            "must_match_target_gameweek": True,
            "owner_change_requires_rebuild": True,
        },
        "stages": {stage: {"status": "pending"} for stage in STAGES},
        "combined": {"status": "pending", "legacy_pointer_path": f"airsenal/latest/entry-{entry_id}.json"},
        "privacy": {"classification": "PRIVATE_MANAGER_CHAT_BRIDGE", "public_upload_forbidden": True},
    }


def _with_worktree(private_repo: Path, branch: str):
    private_repo, before = legacy.validate_private_repo(private_repo)
    if not legacy.branch_exists(private_repo, branch):
        raise StagedPublishError(f"results branch {branch!r} does not exist")
    legacy.git(private_repo, "fetch", "--quiet", "origin", branch)
    temp = tempfile.TemporaryDirectory(prefix="airsenal-staged-bridge-")
    worktree = Path(temp.name) / "results"
    legacy.git(private_repo, "worktree", "add", "--detach", str(worktree), f"origin/{branch}")
    legacy.ensure_author(worktree)
    if legacy.git_text(worktree, "status", "--porcelain"):
        raise StagedPublishError("temporary results worktree is dirty")
    return private_repo, before, temp, worktree


def _finish_worktree(private_repo: Path, before: str, temp: tempfile.TemporaryDirectory, worktree: Path) -> None:
    legacy.git(private_repo, "worktree", "remove", "--force", str(worktree), check=False)
    legacy.git(private_repo, "worktree", "prune", check=False)
    temp.cleanup()
    after = legacy.git_text(private_repo, "status", "--porcelain=v1", "--untracked-files=all")
    if after != before:
        raise StagedPublishError("normal private working tree changed during staged publication")


def initialize(*, private_repo: Path, entry_id: int, target_gw: int, producer_run_id: str, owner_state_path: Path, official_path: Path, experiment_sha: str, upstream_sha: str, scenario_path: Path | None, branch: str = RESULTS_BRANCH) -> dict[str, Any]:
    owner_sha = sha256_file(owner_state_path)
    official_sha = sha256_file(official_path)
    scenario_sha = sha256_file(scenario_path) if scenario_path else None
    started_at = utcnow()
    manifest = _base_manifest(
        entry_id=entry_id,
        target_gw=target_gw,
        producer_run_id=producer_run_id,
        owner_sha=owner_sha,
        official_sha=official_sha,
        experiment_sha=experiment_sha,
        upstream_sha=upstream_sha,
        scenario_sha=scenario_sha,
        started_at=started_at,
    )
    repo, before, temp, worktree = _with_worktree(private_repo, branch)
    try:
        rel = manifest_rel(entry_id)
        legacy.write_json(worktree / rel, manifest)
        legacy.git(worktree, "add", str(rel))
        legacy.git(worktree, "commit", "-m", f"Start AIrsenal producer entry {entry_id} GW{target_gw} {producer_run_id}")
        commit = legacy.git_text(worktree, "rev-parse", "HEAD")
        legacy.git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
    finally:
        _finish_worktree(repo, before, temp, worktree)
    return {"status": "running", "manifest_path": str(manifest_rel(entry_id)), "manifest_commit_sha": commit}


def _stage_schema(stage: str) -> str:
    return "airsenal-chat-prediction-stage-v1" if stage == "forecast" else "airsenal-chat-horizon-result-v1"


def publish_stage(*, private_repo: Path, stage: str, artifact_path: Path, owner_state_path: Path, official_path: Path, producer_run_id: str, experiment_sha: str, upstream_sha: str, scenario_path: Path | None, branch: str = RESULTS_BRANCH) -> dict[str, Any]:
    if stage not in STAGES:
        raise StagedPublishError(f"unsupported stage {stage!r}")
    artifact = load_json(artifact_path)
    if artifact.get("schema") != _stage_schema(stage):
        raise StagedPublishError(f"{stage} artifact schema mismatch")
    entry_id = int(artifact.get("entry_id") or 0)
    target_gw = int(artifact.get("target_gameweek") or 0)
    if entry_id <= 0 or target_gw <= 0:
        raise StagedPublishError(f"{stage} artifact owner scope invalid")
    if stage == "h3" and int(artifact.get("horizon") or 0) != 3:
        raise StagedPublishError("H3 artifact horizon mismatch")
    if stage == "h5" and int(artifact.get("horizon") or 0) != 5:
        raise StagedPublishError("H5 artifact horizon mismatch")
    if stage == "forecast" and int(artifact.get("horizon") or 0) != 5:
        raise StagedPublishError("forecast must cover H5 gameweek range")
    owner_sha = sha256_file(owner_state_path)
    official_sha = sha256_file(official_path)
    scenario_sha = sha256_file(scenario_path) if scenario_path else None
    artifact_sha = sha256_file(artifact_path)
    generated_at = str(artifact.get("generated_at") or utcnow())
    immutable_root = Path("airsenal") / "staged" / "runs" / f"entry-{entry_id}" / f"gw{target_gw}" / producer_run_id / stage
    stage_manifest_rel = immutable_root / "stage_manifest.json"
    artifact_rel = immutable_root / "artifact.json"
    owner_rel = immutable_root / "owner_state.json"
    official_rel = immutable_root / "official_fpl_provenance.json"

    repo, before, temp, worktree = _with_worktree(private_repo, branch)
    try:
        latest_manifest_path = worktree / manifest_rel(entry_id)
        if not latest_manifest_path.is_file():
            raise StagedPublishError("producer manifest missing; initialize before publishing stages")
        manifest = load_json(latest_manifest_path)
        if manifest.get("schema") != PRODUCER_MANIFEST_SCHEMA or manifest.get("producer_run_id") != producer_run_id:
            raise StagedPublishError("producer manifest does not belong to this run")
        if int(manifest.get("target_gameweek") or 0) != target_gw or manifest.get("owner_state_sha256") != owner_sha:
            raise StagedPublishError("producer manifest owner/Gameweek mismatch")
        target = worktree / immutable_root
        if target.exists():
            raise StagedPublishError(f"immutable stage path already exists: {immutable_root}")
        target.mkdir(parents=True)
        shutil.copy2(artifact_path, worktree / artifact_rel)
        shutil.copy2(owner_state_path, worktree / owner_rel)
        shutil.copy2(official_path, worktree / official_rel)
        stage_manifest = {
            "schema": STAGE_MANIFEST_SCHEMA,
            "status": "ready",
            "stage": stage,
            "entry_id": entry_id,
            "target_gameweek": target_gw,
            "producer_run_id": producer_run_id,
            "generated_at": generated_at,
            "artifact_sha256": artifact_sha,
            "owner_state_sha256": owner_sha,
            "official_fpl_provenance_sha256": official_sha,
            "public_experiment_sha": experiment_sha,
            "upstream_airsenal_sha": upstream_sha,
            "scenario_sha256": scenario_sha,
            "privacy": {"classification": "PRIVATE_MANAGER_CHAT_BRIDGE", "public_upload_forbidden": True},
        }
        legacy.write_json(worktree / stage_manifest_rel, stage_manifest)
        legacy.git(worktree, "add", str(immutable_root))
        legacy.git(worktree, "commit", "-m", f"Persist AIrsenal {stage} entry {entry_id} GW{target_gw} {producer_run_id}")
        run_commit = legacy.git_text(worktree, "rev-parse", "HEAD")
        legacy.git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")

        updated_at = utcnow()
        pointer = {
            "schema": STAGED_POINTER_SCHEMA,
            "status": "ready",
            "repository": PRIVATE_REPO_SLUG,
            "branch": branch,
            "stage": stage,
            "entry_id": entry_id,
            "target_gameweek": target_gw,
            "producer_run_id": producer_run_id,
            "updated_at": updated_at,
            "run_commit_sha": run_commit,
            "artifact_path": str(artifact_rel),
            "artifact_sha256": artifact_sha,
            "stage_manifest_path": str(stage_manifest_rel),
            "owner_state_path": str(owner_rel),
            "owner_state_sha256": owner_sha,
            "official_fpl_provenance_path": str(official_rel),
            "official_fpl_provenance_sha256": official_sha,
            "public_experiment_sha": experiment_sha,
            "upstream_airsenal_sha": upstream_sha,
            "scenario_sha256": scenario_sha,
        }
        manifest["updated_at"] = updated_at
        manifest["stages"][stage] = {
            "status": "ready",
            "pointer_path": str(pointer_rel(entry_id, stage)),
            "run_commit_sha": run_commit,
            "artifact_sha256": artifact_sha,
            "generated_at": generated_at,
        }
        legacy.write_json(worktree / pointer_rel(entry_id, stage), pointer)
        legacy.write_json(latest_manifest_path, manifest)
        legacy.git(worktree, "add", str(pointer_rel(entry_id, stage)), str(manifest_rel(entry_id)))
        legacy.git(worktree, "commit", "-m", f"Point AIrsenal {stage} latest for entry {entry_id} GW{target_gw}")
        pointer_commit = legacy.git_text(worktree, "rev-parse", "HEAD")
        legacy.git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
    finally:
        _finish_worktree(repo, before, temp, worktree)
    return {
        "status": "ready",
        "stage": stage,
        "entry_id": entry_id,
        "target_gameweek": target_gw,
        "producer_run_id": producer_run_id,
        "run_commit_sha": run_commit,
        "pointer_commit_sha": pointer_commit,
        "pointer_path": str(pointer_rel(entry_id, stage)),
        "artifact_path": str(artifact_rel),
        "artifact_sha256": artifact_sha,
    }


def mark_failed(*, private_repo: Path, entry_id: int, producer_run_id: str, stage: str, failure_class: str, branch: str = RESULTS_BRANCH) -> None:
    if stage not in STAGES:
        raise StagedPublishError(f"unsupported stage {stage!r}")
    safe = "".join(ch for ch in failure_class.upper() if ch.isalnum() or ch == "_")[:80] or "FAILED"
    repo, before, temp, worktree = _with_worktree(private_repo, branch)
    try:
        path = worktree / manifest_rel(entry_id)
        manifest = load_json(path)
        if manifest.get("producer_run_id") != producer_run_id:
            raise StagedPublishError("refusing to mark failure on a newer producer manifest")
        manifest["updated_at"] = utcnow()
        manifest["status"] = "degraded"
        manifest["stages"][stage] = {"status": "failed", "failure_class": safe}
        legacy.write_json(path, manifest)
        legacy.git(worktree, "add", str(manifest_rel(entry_id)))
        legacy.git(worktree, "commit", "-m", f"Mark AIrsenal {stage} failed for producer {producer_run_id}")
        legacy.git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
    finally:
        _finish_worktree(repo, before, temp, worktree)


def mark_combined_ready(*, private_repo: Path, entry_id: int, producer_run_id: str, legacy_result: dict[str, Any], branch: str = RESULTS_BRANCH) -> dict[str, Any]:
    repo, before, temp, worktree = _with_worktree(private_repo, branch)
    try:
        path = worktree / manifest_rel(entry_id)
        manifest = load_json(path)
        if manifest.get("producer_run_id") != producer_run_id:
            raise StagedPublishError("refusing to finalize a newer producer manifest")
        manifest["updated_at"] = utcnow()
        manifest["status"] = "ready"
        manifest["combined"] = {
            "status": "ready",
            "legacy_pointer_path": str(legacy_result["pointer_path"]),
            "run_commit_sha": legacy_result["run_commit_sha"],
            "context_sha256": legacy_result["context_sha256"],
        }
        legacy.write_json(path, manifest)
        legacy.git(worktree, "add", str(manifest_rel(entry_id)))
        legacy.git(worktree, "commit", "-m", f"Finalize AIrsenal producer {producer_run_id}")
        commit = legacy.git_text(worktree, "rev-parse", "HEAD")
        legacy.git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
    finally:
        _finish_worktree(repo, before, temp, worktree)
    return {"status": "ready", "manifest_path": str(manifest_rel(entry_id)), "manifest_commit_sha": commit}
