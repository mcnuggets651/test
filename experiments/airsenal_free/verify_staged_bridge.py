#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import verify_chat_bridge as legacy_verify

PRIVATE_REPO_SLUG = legacy_verify.PRIVATE_REPO_SLUG
RESULTS_BRANCH = legacy_verify.RESULTS_BRANCH
MANIFEST_SCHEMA = "airsenal-producer-manifest-v1"
POINTER_SCHEMA = "airsenal-staged-pointer-v1"
STAGE_MANIFEST_SCHEMA = "airsenal-stage-run-v1"
STAGES = ("forecast", "h3", "h5")


class VerifyStagedError(RuntimeError):
    pass


def parse_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifyStagedError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise VerifyStagedError(f"{label} must be an object")
    return value


def verify(*, private_repo: Path, entry_id: int = 63984, branch: str = RESULTS_BRANCH, verify_combined: bool = True) -> dict[str, Any]:
    repo = private_repo.expanduser().resolve()
    if not repo.is_dir():
        raise VerifyStagedError(f"private repository does not exist: {repo}")
    top = Path(legacy_verify.git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise VerifyStagedError("private repository must be its Git toplevel")
    remote = legacy_verify.git_text(repo, "remote", "get-url", "origin")
    if (legacy_verify.github_slug(remote) or "").lower() != PRIVATE_REPO_SLUG.lower():
        raise VerifyStagedError(f"private repository origin must be {PRIVATE_REPO_SLUG}")
    legacy_verify.git(repo, "fetch", "--quiet", "origin", branch)
    branch_ref = f"origin/{branch}"
    manifest_path = f"airsenal/latest/entry-{int(entry_id)}/manifest.json"
    manifest = parse_json(legacy_verify.show_bytes(repo, branch_ref, manifest_path), "producer manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise VerifyStagedError("producer manifest schema mismatch")
    if manifest.get("repository") != PRIVATE_REPO_SLUG or manifest.get("branch") != branch:
        raise VerifyStagedError("producer manifest repository/branch mismatch")
    if int(manifest.get("entry_id") or 0) != int(entry_id):
        raise VerifyStagedError("producer manifest entry mismatch")
    producer_run_id = str(manifest.get("producer_run_id") or "")
    if not producer_run_id:
        raise VerifyStagedError("producer manifest run id missing")
    target_gw = int(manifest.get("target_gameweek") or 0)
    owner_sha = str(manifest.get("owner_state_sha256") or "")
    if target_gw <= 0 or not re.fullmatch(r"[0-9a-f]{64}", owner_sha):
        raise VerifyStagedError("producer manifest scope/hash invalid")

    verified_stages: dict[str, Any] = {}
    for stage in STAGES:
        state = (manifest.get("stages") or {}).get(stage) or {}
        status = str(state.get("status") or "pending")
        if status != "ready":
            verified_stages[stage] = {"status": status, "verified": False}
            continue
        pointer_path = str(state.get("pointer_path") or f"airsenal/latest/entry-{entry_id}/{stage}.json")
        pointer = parse_json(legacy_verify.show_bytes(repo, branch_ref, pointer_path), f"{stage} pointer")
        if pointer.get("schema") != POINTER_SCHEMA or pointer.get("stage") != stage:
            raise VerifyStagedError(f"{stage} pointer schema/stage mismatch")
        if pointer.get("producer_run_id") != producer_run_id:
            raise VerifyStagedError(f"{stage} pointer belongs to another producer run")
        if int(pointer.get("target_gameweek") or 0) != target_gw:
            raise VerifyStagedError(f"{stage} pointer Gameweek mismatch")
        if pointer.get("owner_state_sha256") != owner_sha:
            raise VerifyStagedError(f"{stage} pointer owner hash mismatch")
        run_commit = str(pointer.get("run_commit_sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", run_commit):
            raise VerifyStagedError(f"{stage} run commit invalid")
        if legacy_verify.git(repo, "merge-base", "--is-ancestor", run_commit, branch_ref, check=False).returncode != 0:
            raise VerifyStagedError(f"{stage} run commit is not on results branch")
        artifact_path = str(pointer.get("artifact_path") or "")
        stage_manifest_path = str(pointer.get("stage_manifest_path") or "")
        if f"/entry-{entry_id}/gw{target_gw}/{producer_run_id}/{stage}/" not in f"/{artifact_path}":
            raise VerifyStagedError(f"{stage} artifact path outside immutable run root")
        artifact_bytes = legacy_verify.show_bytes(repo, run_commit, artifact_path)
        artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
        if artifact_sha != pointer.get("artifact_sha256"):
            raise VerifyStagedError(f"{stage} artifact hash mismatch")
        stage_manifest = parse_json(legacy_verify.show_bytes(repo, run_commit, stage_manifest_path), f"{stage} stage manifest")
        if stage_manifest.get("schema") != STAGE_MANIFEST_SCHEMA or stage_manifest.get("stage") != stage:
            raise VerifyStagedError(f"{stage} immutable stage manifest mismatch")
        if stage_manifest.get("artifact_sha256") != artifact_sha or stage_manifest.get("owner_state_sha256") != owner_sha:
            raise VerifyStagedError(f"{stage} immutable stage manifest integrity mismatch")
        verified_stages[stage] = {
            "status": "ready",
            "verified": True,
            "run_commit_sha": run_commit,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha,
        }

    combined_state = manifest.get("combined") or {}
    combined_verified = False
    combined = None
    if verify_combined and combined_state.get("status") == "ready":
        combined = legacy_verify.verify(private_repo=repo, entry_id=entry_id, branch=branch)
        if combined["target_gameweek"] != target_gw:
            raise VerifyStagedError("combined pointer Gameweek differs from producer manifest")
        if combined_state.get("run_commit_sha") != combined["run_commit_sha"]:
            raise VerifyStagedError("combined producer manifest run commit mismatch")
        if combined_state.get("context_sha256") != combined["context_sha256"]:
            raise VerifyStagedError("combined producer manifest context hash mismatch")
        combined_verified = True

    return {
        "status": manifest.get("status"),
        "repository": PRIVATE_REPO_SLUG,
        "branch": branch,
        "entry_id": int(entry_id),
        "target_gameweek": target_gw,
        "producer_run_id": producer_run_id,
        "manifest_path": manifest_path,
        "manifest_head_sha": legacy_verify.git_text(repo, "rev-parse", branch_ref),
        "owner_state_sha256": owner_sha,
        "stages": verified_stages,
        "combined": {"status": combined_state.get("status"), "verified": combined_verified, "legacy": combined},
        "exact_commit_retrieval_verified": all(not v.get("status") == "ready" or v.get("verified") for v in verified_stages.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify staged AIrsenal producer manifest and immutable stage pointers")
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument("--entry-id", type=int, default=63984)
    parser.add_argument("--branch", default=RESULTS_BRANCH)
    args = parser.parse_args(argv)
    try:
        result = verify(private_repo=args.private_repo, entry_id=args.entry_id, branch=args.branch)
    except (VerifyStagedError, legacy_verify.VerifyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
