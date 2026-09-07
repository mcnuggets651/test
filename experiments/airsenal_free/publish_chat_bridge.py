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
import tempfile
from pathlib import Path
from typing import Any

PRIVATE_REPO_SLUG = "mcnuggets651/fpl"
RESULTS_BRANCH = "airsenal-results"
CONTEXT_SCHEMA = "airsenal-chat-decision-context-v1"
POINTER_SCHEMA = "airsenal-chat-bridge-latest-v1"
RUN_SCHEMA = "airsenal-chat-bridge-run-v1"
HEALTH_SCHEMA = "airsenal-chat-health-v1"
REQUIRED_LOCAL_FILES = (
    "decision_context.json",
    "decision_context.sha256",
    "owner_state.json",
    "official_fpl_provenance.json",
    "run_manifest.json",
    "h3/result.json",
    "h5/result.json",
)


class PublishError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PublishError(f"expected JSON object: {path}")
    return value


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(f"git {' '.join(args)} failed: {detail[:500]}")
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


def validate_private_repo(repo: Path, *, require_github_origin: bool = True) -> tuple[Path, str]:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise PublishError(f"private repository does not exist: {repo}")
    top = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise PublishError(f"private repository must be its Git toplevel: {top}")
    if require_github_origin:
        remote = git_text(repo, "remote", "get-url", "origin")
        if (github_slug(remote) or "").lower() != PRIVATE_REPO_SLUG.lower():
            raise PublishError(f"private repository origin must be {PRIVATE_REPO_SLUG}")
    return repo, git_text(repo, "status", "--porcelain=v1", "--untracked-files=all")


def validate_bundle(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    missing = [name for name in REQUIRED_LOCAL_FILES if not (run_dir / name).is_file()]
    if missing:
        raise PublishError(f"private run bundle is incomplete: {missing}")
    context_path = run_dir / "decision_context.json"
    context = load_json(context_path)
    if context.get("schema") != CONTEXT_SCHEMA:
        raise PublishError(f"unexpected decision context schema {context.get('schema')!r}")
    privacy = context.get("privacy") or {}
    if privacy.get("classification") != "PRIVATE_MANAGER_LOCAL_ONLY" or privacy.get("safe_for_public_artifact_upload") is not False:
        raise PublishError("decision context privacy declaration is invalid")
    entry_id = int(context.get("entry_id") or 0)
    gameweek = int(context.get("target_gameweek") or 0)
    if entry_id <= 0 or gameweek <= 0:
        raise PublishError("decision context entry/gameweek invalid")
    horizons = context.get("airsenal_result") or {}
    if set(horizons) != {"h3", "h5"}:
        raise PublishError("decision context must contain exactly h3 and h5 results")
    context_sha = sha256_file(context_path)
    checksum = (run_dir / "decision_context.sha256").read_text(encoding="utf-8").strip().split()
    if not checksum or checksum[0] != context_sha:
        raise PublishError("decision_context.sha256 does not match context")
    hashes = context.get("integrity") or {}
    for name in ("owner_state.json", "official_fpl_provenance.json", "h3/result.json", "h5/result.json"):
        expected = (hashes.get("files") or {}).get(name)
        if expected != sha256_file(run_dir / name):
            raise PublishError(f"context integrity mismatch for {name}")
    return {
        "run_dir": run_dir,
        "context": context,
        "entry_id": entry_id,
        "gameweek": gameweek,
        "context_sha256": context_sha,
    }


def branch_exists(repo: Path, branch: str) -> bool:
    result = git(repo, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def ensure_author(repo: Path) -> None:
    if not git(repo, "config", "user.name", check=False).stdout.strip():
        git(repo, "config", "user.name", "AIrsenal Local Bridge")
    if not git(repo, "config", "user.email", check=False).stdout.strip():
        git(repo, "config", "user.email", "airsenal-local@users.noreply.github.com")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _stamp(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _health_payload(*, context: dict[str, Any], entry_id: int, gw: int, branch: str, run_id: str, run_commit: str, context_sha256: str, updated_at: str) -> dict[str, Any]:
    provenance = context.get("provenance") or {}
    source = "github-actions-self-hosted" if os.environ.get("GITHUB_ACTIONS") == "true" else "direct-local"
    payload: dict[str, Any] = {
        "schema": HEALTH_SCHEMA,
        "status": "ready",
        "repository": PRIVATE_REPO_SLUG,
        "branch": branch,
        "entry_id": entry_id,
        "target_gameweek": gw,
        "last_success_at": updated_at,
        "run_id": run_id,
        "run_commit_sha": run_commit,
        "context_sha256": context_sha256,
        "public_experiment_sha": provenance.get("experiment_code_sha"),
        "upstream_airsenal_sha": provenance.get("airsenal_upstream_sha"),
        "horizons": [3, 5],
        "execution_surface": source,
        "public_upload_forbidden": True,
    }
    if source == "github-actions-self-hosted":
        payload["github_actions"] = {
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        }
    return payload


def publish(*, run_dir: Path, private_repo: Path, branch: str = RESULTS_BRANCH, require_github_origin: bool = True) -> dict[str, Any]:
    private_repo, before_status = validate_private_repo(private_repo, require_github_origin=require_github_origin)
    bundle = validate_bundle(run_dir)
    if not branch_exists(private_repo, branch):
        raise PublishError(f"private results branch {branch!r} does not exist; governance/bootstrap must create it first")
    context = bundle["context"]
    generated_at = str(context.get("run_timestamp") or "")
    run_id = f"{_stamp(generated_at)}-{bundle['context_sha256'][:12]}"
    entry_id = int(bundle["entry_id"])
    gw = int(bundle["gameweek"])
    run_root = Path("airsenal") / "runs" / f"entry-{entry_id}" / f"gw{gw}" / run_id
    pointer_rel = Path("airsenal") / "latest" / f"entry-{entry_id}.json"
    health_rel = Path("airsenal") / "health" / f"entry-{entry_id}.json"
    context_rel = run_root / "decision_context.json"
    run_manifest_rel = run_root / "bridge_run_manifest.json"

    git(private_repo, "fetch", "--quiet", "origin", branch)
    with tempfile.TemporaryDirectory(prefix="airsenal-chat-bridge-") as tmp:
        worktree = Path(tmp) / "results"
        git(private_repo, "worktree", "add", "--detach", str(worktree), f"origin/{branch}")
        try:
            ensure_author(worktree)
            if git_text(worktree, "status", "--porcelain"):
                raise PublishError("temporary results worktree is unexpectedly dirty")
            target = worktree / run_root
            if target.exists():
                raise PublishError(f"immutable run path already exists: {run_root}")
            target.mkdir(parents=True)
            source = Path(bundle["run_dir"])
            for rel in REQUIRED_LOCAL_FILES:
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / rel, dst)
            published_at = dt.datetime.now(dt.timezone.utc).isoformat()
            bridge_manifest = {
                "schema": RUN_SCHEMA,
                "status": "ready",
                "entry_id": entry_id,
                "target_gameweek": gw,
                "published_at": published_at,
                "context_sha256": bundle["context_sha256"],
                "source_experiment_sha": (context.get("provenance") or {}).get("experiment_code_sha"),
                "upstream_airsenal_sha": (context.get("provenance") or {}).get("airsenal_upstream_sha"),
                "private_authority": context.get("private_authority"),
                "privacy": {
                    "repository": PRIVATE_REPO_SLUG,
                    "branch": branch,
                    "classification": "PRIVATE_MANAGER_CHAT_BRIDGE",
                    "public_upload_forbidden": True,
                },
            }
            write_json(worktree / run_manifest_rel, bridge_manifest)
            git(worktree, "add", str(run_root))
            git(worktree, "commit", "-m", f"Persist AIrsenal chat run entry {entry_id} GW{gw} {run_id}")
            run_commit = git_text(worktree, "rev-parse", "HEAD")
            git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
            remote_after_run = git(private_repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}").stdout.split()[0]
            if remote_after_run != run_commit:
                raise PublishError("remote branch did not advance to immutable run commit")

            updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
            pointer = {
                "schema": POINTER_SCHEMA,
                "status": "ready",
                "repository": PRIVATE_REPO_SLUG,
                "branch": branch,
                "entry_id": entry_id,
                "target_gameweek": gw,
                "updated_at": updated_at,
                "run_id": run_id,
                "run_commit_sha": run_commit,
                "context_path": str(context_rel),
                "context_sha256": bundle["context_sha256"],
                "run_manifest_path": str(run_manifest_rel),
                "owner_state_path": str(run_root / "owner_state.json"),
                "official_fpl_provenance_path": str(run_root / "official_fpl_provenance.json"),
                "h3_result_path": str(run_root / "h3/result.json"),
                "h5_result_path": str(run_root / "h5/result.json"),
                "context_generated_at": generated_at,
                "health_path": str(health_rel),
            }
            health = _health_payload(
                context=context,
                entry_id=entry_id,
                gw=gw,
                branch=branch,
                run_id=run_id,
                run_commit=run_commit,
                context_sha256=bundle["context_sha256"],
                updated_at=updated_at,
            )
            write_json(worktree / pointer_rel, pointer)
            write_json(worktree / health_rel, health)
            git(worktree, "add", str(pointer_rel), str(health_rel))
            git(worktree, "commit", "-m", f"Point AIrsenal latest and health to entry {entry_id} GW{gw} {run_id}")
            pointer_commit = git_text(worktree, "rev-parse", "HEAD")
            git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
            remote_head = git(private_repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}").stdout.split()[0]
            if remote_head != pointer_commit:
                raise PublishError("remote branch did not advance to latest-pointer/health commit")
        finally:
            git(private_repo, "worktree", "remove", "--force", str(worktree), check=False)
            git(private_repo, "worktree", "prune", check=False)
    after_status = git_text(private_repo, "status", "--porcelain=v1", "--untracked-files=all")
    if after_status != before_status:
        raise PublishError("normal private working tree changed during publication")
    return {
        "schema": POINTER_SCHEMA,
        "status": "ready",
        "repository": PRIVATE_REPO_SLUG,
        "branch": branch,
        "entry_id": entry_id,
        "target_gameweek": gw,
        "run_id": run_id,
        "run_commit_sha": run_commit,
        "pointer_commit_sha": pointer_commit,
        "pointer_path": str(pointer_rel),
        "health_path": str(health_rel),
        "context_path": str(context_rel),
        "context_sha256": bundle["context_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument("--branch", default=RESULTS_BRANCH)
    args = parser.parse_args(argv)
    try:
        result = publish(run_dir=args.run_dir, private_repo=args.private_repo, branch=args.branch)
    except (PublishError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
