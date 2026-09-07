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
RESULTS_BRANCH = "dastan-results"
AI_CONTEXT_SCHEMA = "dastan-smartplay-ai-context-v1"
POINTER_SCHEMA = "dastan-chat-bridge-latest-v1"
RUN_SCHEMA = "dastan-chat-bridge-run-v1"
REQUIRED_LOCAL_FILES = (
    "ai_decision_context.json",
    "ai_decision_context.sha256",
    "ai_decision_brief.md",
    "strategy_manifest.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail[:500]}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def github_repo_slug(remote: str) -> str | None:
    value = remote.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    patterns = (
        r"^https?://github\.com/([^/]+/[^/]+)$",
        r"^ssh://git@github\.com/([^/]+/[^/]+)$",
        r"^git@github\.com:([^/]+/[^/]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def validate_private_repo(repo: Path, *, require_github_origin: bool = True) -> Path:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"private repository does not exist: {repo}")
    top = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise ValueError(f"private repository must be its Git toplevel: {top}")
    if require_github_origin:
        remote = git_text(repo, "remote", "get-url", "origin")
        slug = github_repo_slug(remote)
        if slug is None or slug.lower() != PRIVATE_REPO_SLUG.lower():
            raise ValueError(f"private repository origin must be {PRIVATE_REPO_SLUG}, got {remote!r}")
    return repo


def _as_positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _stamp(value: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("AI context generated_at is invalid") from exc
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def validate_local_bundle(state_dir: Path) -> dict[str, Any]:
    state_dir = state_dir.expanduser().resolve()
    missing = [name for name in REQUIRED_LOCAL_FILES if not (state_dir / name).is_file()]
    if missing:
        raise ValueError(f"chat bridge source files are missing: {missing}")

    context_path = state_dir / "ai_decision_context.json"
    context = load_object(context_path, "AI decision context")
    if context.get("schema") != AI_CONTEXT_SCHEMA:
        raise ValueError(f"unexpected AI context schema: {context.get('schema')!r}")
    privacy = context.get("privacy") or {}
    if privacy.get("classification") != "PRIVATE_MANAGER_LOCAL_ONLY":
        raise ValueError("chat bridge only publishes PRIVATE_MANAGER_LOCAL_ONLY context to the private repository")
    if privacy.get("safe_for_public_artifact_upload") is not False:
        raise ValueError("AI context must explicitly declare itself unsafe for public artifact upload")

    scope = context.get("scope") or {}
    entry_id = _as_positive_int(scope.get("entry_id"), "scope.entry_id")
    gameweek = _as_positive_int(scope.get("gameweek"), "scope.gameweek")
    if int(scope.get("projection_horizon") or 0) != 1:
        raise ValueError("chat bridge only publishes the certified GW+1 context")

    digest = sha256_file(context_path)
    checksum = (state_dir / "ai_decision_context.sha256").read_text(encoding="utf-8").strip().split()
    if not checksum or checksum[0] != digest:
        raise ValueError("AI context checksum file does not match ai_decision_context.json")

    strategy_path = state_dir / "strategy_manifest.json"
    strategy = load_object(strategy_path, "strategy manifest")
    if int(strategy.get("entry_id") or 0) != entry_id or int(strategy.get("gameweek") or 0) != gameweek:
        raise ValueError("strategy manifest entry/gameweek does not match AI context")
    context_integrity = context.get("integrity") or {}
    if context_integrity.get("strategy_manifest_sha256") != sha256_file(strategy_path):
        raise ValueError("AI context is not hash-bound to strategy_manifest.json")

    return {
        "state_dir": state_dir,
        "context": context,
        "entry_id": entry_id,
        "gameweek": gameweek,
        "context_sha256": digest,
        "generated_at": str(context.get("generated_at") or ""),
        "strategy_sha256": sha256_file(strategy_path),
    }


def public_experiment_head(script_dir: Path) -> str | None:
    result = git(script_dir, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def branch_exists(repo: Path, branch: str) -> bool:
    result = git(repo, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def ensure_author(worktree: Path) -> None:
    if not git_text(worktree, "config", "user.name"):
        git(worktree, "config", "user.name", "Dastan Local Bridge")
    if not git_text(worktree, "config", "user.email"):
        git(worktree, "config", "user.email", "dastan-local@users.noreply.github.com")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def publish(
    *,
    state_dir: Path,
    private_repo: Path,
    branch: str = RESULTS_BRANCH,
    require_github_origin: bool = True,
    script_dir: Path | None = None,
) -> dict[str, Any]:
    private_repo = validate_private_repo(private_repo, require_github_origin=require_github_origin)
    bundle = validate_local_bundle(state_dir)
    if not branch_exists(private_repo, branch):
        raise RuntimeError(
            f"private results branch {branch!r} does not exist; create it once from {PRIVATE_REPO_SLUG}:main"
        )

    entry_id = int(bundle["entry_id"])
    gameweek = int(bundle["gameweek"])
    context_sha = str(bundle["context_sha256"])
    run_id = f"{_stamp(str(bundle['generated_at']))}-{context_sha[:12]}"
    run_dir_rel = Path("dastan") / "runs" / f"entry-{entry_id}" / f"gw{gameweek}" / run_id
    context_rel = run_dir_rel / "ai_decision_context.json"
    brief_rel = run_dir_rel / "ai_decision_brief.md"
    checksum_rel = run_dir_rel / "ai_decision_context.sha256"
    strategy_rel = run_dir_rel / "strategy_manifest.json"
    run_manifest_rel = run_dir_rel / "bridge_run_manifest.json"
    pointer_rel = Path("dastan") / "latest" / f"entry-{entry_id}.json"

    git(private_repo, "fetch", "--quiet", "origin", branch)

    with tempfile.TemporaryDirectory(prefix="dastan-chat-bridge-") as temp_raw:
        temp = Path(temp_raw)
        worktree = temp / "results"
        git(private_repo, "worktree", "add", "--detach", str(worktree), f"origin/{branch}")
        try:
            ensure_author(worktree)
            if git_text(worktree, "status", "--porcelain"):
                raise RuntimeError("temporary results worktree is unexpectedly dirty")
            target_run_dir = worktree / run_dir_rel
            if target_run_dir.exists():
                raise RuntimeError(f"immutable run path already exists: {run_dir_rel}")
            target_run_dir.mkdir(parents=True)

            source = Path(bundle["state_dir"])
            for name in ("ai_decision_context.json", "ai_decision_context.sha256", "ai_decision_brief.md", "strategy_manifest.json"):
                shutil.copy2(source / name, target_run_dir / name)

            context = bundle["context"]
            run_manifest = {
                "schema": RUN_SCHEMA,
                "status": "ready",
                "entry_id": entry_id,
                "gameweek": gameweek,
                "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "context_generated_at": bundle["generated_at"],
                "context_sha256": context_sha,
                "strategy_manifest_sha256": bundle["strategy_sha256"],
                "source_public_experiment_head": public_experiment_head(script_dir or Path(__file__).resolve().parent),
                "private_authority": context.get("private_authority"),
                "scope": context.get("scope"),
                "privacy": {
                    "repository": PRIVATE_REPO_SLUG,
                    "branch": branch,
                    "classification": "PRIVATE_MANAGER_CHAT_BRIDGE",
                    "public_upload_forbidden": True,
                },
            }
            _write_json(worktree / run_manifest_rel, run_manifest)

            git(worktree, "add", str(run_dir_rel))
            git(worktree, "commit", "-m", f"Persist Dastan chat run entry {entry_id} GW{gameweek} {run_id}")
            run_commit = git_text(worktree, "rev-parse", "HEAD")
            git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")

            pointer = {
                "schema": POINTER_SCHEMA,
                "status": "ready",
                "repository": PRIVATE_REPO_SLUG,
                "branch": branch,
                "entry_id": entry_id,
                "gameweek": gameweek,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "run_commit_sha": run_commit,
                "run_id": run_id,
                "run_manifest_path": str(run_manifest_rel),
                "context_path": str(context_rel),
                "context_sha256": context_sha,
                "brief_path": str(brief_rel),
                "checksum_path": str(checksum_rel),
                "strategy_manifest_path": str(strategy_rel),
                "context_generated_at": bundle["generated_at"],
            }
            _write_json(worktree / pointer_rel, pointer)
            git(worktree, "add", str(pointer_rel))
            git(worktree, "commit", "-m", f"Point Dastan latest to entry {entry_id} GW{gameweek} {run_id}")
            pointer_commit = git_text(worktree, "rev-parse", "HEAD")
            git(worktree, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")

            remote = git(private_repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
            remote_head = remote.stdout.split()[0] if remote.stdout.split() else ""
            if remote_head != pointer_commit:
                raise RuntimeError("remote results branch did not advance to the pointer commit")

            return {
                "schema": POINTER_SCHEMA,
                "status": "ready",
                "repository": PRIVATE_REPO_SLUG,
                "branch": branch,
                "entry_id": entry_id,
                "gameweek": gameweek,
                "run_id": run_id,
                "run_commit_sha": run_commit,
                "pointer_commit_sha": pointer_commit,
                "pointer_path": str(pointer_rel),
                "context_path": str(context_rel),
                "context_sha256": context_sha,
            }
        finally:
            git(private_repo, "worktree", "remove", "--force", str(worktree), check=False)
            git(private_repo, "worktree", "prune", check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a completed private Dastan AI context to the immutable Git chat bridge"
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument("--branch", default=RESULTS_BRANCH)
    args = parser.parse_args(argv)
    try:
        result = publish(state_dir=args.state_dir, private_repo=args.private_repo, branch=args.branch)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
