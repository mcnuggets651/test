#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PRIVATE_REPO_SLUG = "mcnuggets651/fpl"
OPERATIONAL_SCHEMA = "dastan-smartplay-operational-v3"
STRATEGY_SCHEMA = "dastan-smartplay-free-strategy-v2"
AI_SIDECAR_FILES = (
    "ai_decision_context.json",
    "ai_decision_context.sha256",
    "ai_decision_brief.md",
)
QUERY_FILES = (
    "tools/apex_strategy_query.py",
    "tools/apex_private_query.py",
    "tools/apex_private_query_entry.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024, b""), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed for {repo}")
    return result.stdout.strip()


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


def validate_private_repo(repo: Path) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"private repo directory does not exist: {repo}")
    top = Path(git_output(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise ValueError(f"--private-repo must point at the repository root: {top}")
    remote = git_output(repo, "remote", "get-url", "origin")
    slug = github_repo_slug(remote)
    if slug is None or slug.lower() != PRIVATE_REPO_SLUG.lower():
        raise ValueError(f"private repo origin must be {PRIVATE_REPO_SLUG}, got {remote!r}")
    missing = [rel for rel in QUERY_FILES if not (repo / rel).is_file()]
    if missing:
        raise ValueError(f"private query bridge is incomplete; missing: {missing}")
    dirty = git_output(repo, "status", "--porcelain", "--", *QUERY_FILES)
    if dirty:
        raise ValueError("private query bridge files have local modifications; refusing to execute them")
    head = git_output(repo, "rev-parse", "HEAD")
    return {
        "path": repo,
        "slug": slug,
        "head": head,
        "query_files": {rel: sha256_file(repo / rel) for rel in QUERY_FILES},
    }


def resolve_private_repo(explicit: Path | None, public_repo_root: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("FPL_PRIVATE_REPO", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if public_repo_root is not None:
        sibling = public_repo_root.parent / "fpl"
        if sibling.is_dir():
            return sibling.resolve()
    raise ValueError(
        "private repo not found; pass --private-repo /path/to/fpl or set FPL_PRIVATE_REPO"
    )


def resolve_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    gh = shutil.which("gh")
    if gh:
        result = subprocess.run(
            [gh, "auth", "token"],
            text=True,
            capture_output=True,
            check=False,
        )
        token = result.stdout.strip() if result.returncode == 0 else ""
        if token:
            return token
    raise ValueError("GitHub authentication unavailable; set GITHUB_TOKEN or authenticate the gh CLI")


def query_latest_private_snapshot(repo_info: dict[str, Any], token: str, output: Path) -> None:
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
        raise RuntimeError(f"authority-aware private strategy query failed with exit code {result.returncode}")
    if not output.is_file():
        raise RuntimeError("private strategy query succeeded without producing its snapshot")
    os.chmod(output, 0o600)


def snapshot_identity(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("private strategy snapshot must be a JSON object")
    run = payload.get("run") or {}
    state = payload.get("team_state") or {}
    if run.get("attestation_scope") != "PRIVATE_MANAGER" or run.get("immutable") is not True:
        raise ValueError("private query did not return an immutable PRIVATE_MANAGER snapshot")
    if state.get("state_complete_for_transfers") is not True:
        raise ValueError("private snapshot is not transfer-complete")
    entry_id = int(state.get("entry_id") or 0)
    gameweek = int(run.get("target_gameweek") or 0)
    if entry_id <= 0 or gameweek <= 0:
        raise ValueError("private snapshot is missing entry_id/target_gameweek")
    return {
        "entry_id": entry_id,
        "gameweek": gameweek,
        "run_id": run.get("run_id"),
        "release_tag": run.get("release_tag"),
        "published_at": run.get("published_at"),
        "snapshot_sha256": sha256_file(path),
    }


def build_strategy_command(
    *,
    root: Path,
    snapshot: Path,
    output_dir: Path,
    posture: str,
    force_refresh: bool,
    plan_b: bool,
    no_hits: bool,
    max_projection_age_hours: float,
) -> list[str]:
    command = [
        sys.executable,
        str(root / "strategy.py"),
        "--apex-strategy-snapshot",
        str(snapshot),
        "--output-dir",
        str(output_dir),
        "--posture",
        posture,
        "--max-projection-age-hours",
        str(max_projection_age_hours),
    ]
    if force_refresh:
        command.append("--force-refresh")
    if plan_b:
        command.append("--plan-b")
    if no_hits:
        command.append("--no-hits")
    return command


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another operational solve already holds {path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_strategy_manifest(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != STRATEGY_SCHEMA:
        raise RuntimeError(f"unexpected strategy manifest schema: {payload.get('schema')!r}")
    if int(payload.get("entry_id") or 0) != identity["entry_id"]:
        raise RuntimeError("strategy manifest entry_id does not match private snapshot")
    if int(payload.get("gameweek") or 0) != identity["gameweek"]:
        raise RuntimeError("strategy manifest gameweek does not match private snapshot")
    state = payload.get("team_state") or {}
    if state.get("source_sha256") != identity["snapshot_sha256"]:
        raise RuntimeError("strategy manifest is not hash-bound to the queried private snapshot")
    return payload


def clear_ai_sidecar(output_dir: Path) -> None:
    for name in AI_SIDECAR_FILES:
        (output_dir / name).unlink(missing_ok=True)


def generate_ai_sidecar(snapshot: Path, output_dir: Path) -> dict[str, Any]:
    import ai_context

    clear_ai_sidecar(output_dir)
    try:
        return ai_context.write_bundle(output_dir, snapshot)
    except Exception:
        clear_ai_sidecar(output_dir)
        raise


def publish_chat_sidecar(output_dir: Path, private_repo: Path) -> dict[str, Any]:
    import publish_chat_bridge

    return publish_chat_bridge.publish(
        state_dir=output_dir,
        private_repo=private_repo,
        script_dir=Path(__file__).resolve().parent,
    )


def public_repo_root(root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Operational £0 owner solve: private authority-aware state -> live Dastan -> "
            "SmartPlay Solver -> read-only AI context -> private Git chat bridge"
        )
    )
    parser.add_argument("--private-repo", type=Path, help="local checkout of mcnuggets651/fpl")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--posture", choices=("neutral", "protect", "chase"), default="neutral")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--plan-b", action="store_true")
    parser.add_argument("--no-hits", action="store_true")
    parser.add_argument(
        "--no-chat-publish",
        action="store_true",
        help="keep the result local instead of syncing the private ChatGPT-readable bridge",
    )
    parser.add_argument("--max-projection-age-hours", type=float, default=6.0)
    args = parser.parse_args(argv)
    if args.max_projection_age_hours <= 0:
        parser.error("--max-projection-age-hours must be positive")

    root = Path(__file__).resolve().parent
    strategy = root / "strategy.py"
    if not strategy.is_file():
        raise RuntimeError(f"strategy.py not found next to operational.py: {strategy}")
    public_repo = public_repo_root(root)
    private_repo = resolve_private_repo(args.private_repo, public_repo)
    repo_info = validate_private_repo(private_repo)
    token = resolve_github_token()

    with tempfile.TemporaryDirectory(prefix="dastan-smartplay-private-") as temp_raw:
        temp = Path(temp_raw)
        os.chmod(temp, 0o700)
        snapshot = temp / "strategy_snapshot.json"
        query_latest_private_snapshot(repo_info, token, snapshot)
        identity = snapshot_identity(snapshot)

        output_dir = (
            args.output_dir.expanduser().resolve()
            if args.output_dir
            else (Path.home() / ".local" / "share" / "dastan-smartplay-free" /
                  f"entry-{identity['entry_id']}" / f"gw{identity['gameweek']}").resolve()
        )
        if public_repo is not None and is_within(output_dir, public_repo):
            parser.error("operational output must remain outside the public Git worktree")
        if is_within(output_dir, private_repo):
            parser.error("operational output must remain outside the private Git worktree")
        output_dir.mkdir(parents=True, exist_ok=True)
        lock_path = Path.home() / ".cache" / "dastan-smartplay-free" / f"entry-{identity['entry_id']}.lock"
        with exclusive_lock(lock_path):
            command = build_strategy_command(
                root=root,
                snapshot=snapshot,
                output_dir=output_dir,
                posture=args.posture,
                force_refresh=args.force_refresh,
                plan_b=args.plan_b,
                no_hits=args.no_hits,
                max_projection_age_hours=args.max_projection_age_hours,
            )
            completed = subprocess.run(command, cwd=root, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"strategy.py failed with exit code {completed.returncode}")
            strategy_manifest_path = output_dir / "strategy_manifest.json"
            if not strategy_manifest_path.is_file():
                raise RuntimeError("strategy.py completed without strategy_manifest.json")
            validate_strategy_manifest(strategy_manifest_path, identity)

            try:
                ai_metadata = generate_ai_sidecar(snapshot, output_dir)
            except Exception as exc:
                ai_metadata = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "core_model_result_valid": True,
                }
                print(
                    "WARNING: core Dastan/SmartPlay solve succeeded, but AI decision context generation failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    file=sys.stderr,
                )

            if ai_metadata.get("status") != "ready":
                chat_metadata: dict[str, Any] = {
                    "status": "skipped",
                    "reason": "ai_context_unavailable",
                    "core_model_result_valid": True,
                }
            elif args.no_chat_publish:
                chat_metadata = {
                    "status": "disabled",
                    "reason": "--no-chat-publish",
                    "core_model_result_valid": True,
                }
            else:
                try:
                    chat_metadata = publish_chat_sidecar(output_dir, private_repo)
                except Exception as exc:
                    chat_metadata = {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "core_model_result_valid": True,
                    }
                    print(
                        "WARNING: core Dastan/SmartPlay solve and AI context succeeded, but private chat sync failed: "
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                        file=sys.stderr,
                    )

            operational_manifest = {
                "schema": OPERATIONAL_SCHEMA,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "entry_id": identity["entry_id"],
                "gameweek": identity["gameweek"],
                "private_authority": {
                    "repository": PRIVATE_REPO_SLUG,
                    "query_code_head": repo_info["head"],
                    "query_file_sha256": repo_info["query_files"],
                    "manager_run_id": identity["run_id"],
                    "manager_release_tag": identity["release_tag"],
                    "manager_published_at": identity["published_at"],
                    "snapshot_sha256": identity["snapshot_sha256"],
                },
                "strategy_manifest_sha256": sha256_file(strategy_manifest_path),
                "ai_decision_context": ai_metadata,
                "chat_bridge": chat_metadata,
                "operational_contract": {
                    "horizon": 1,
                    "owner_state": "authority-aware latest immutable PRIVATE_MANAGER",
                    "private_snapshot_retained": False,
                    "ai_layer": "read-only post-solve sidecar",
                    "ai_failure_invalidates_core_model": False,
                    "chat_bridge": "private Git immutable run commit plus latest pointer",
                    "chat_publish_failure_invalidates_core_model": False,
                    "cost": "zero_paid_services",
                },
            }
            operational_manifest_path = output_dir / "operational_manifest.json"
            operational_manifest_path.write_text(
                json.dumps(operational_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"Operational solve complete: {output_dir}")
            print(f"Recommendation: {output_dir / 'solution' / 'summary.md'}")
            if ai_metadata.get("status") == "ready":
                print(f"AI decision context: {output_dir / str(ai_metadata['context_file'])}")
                print(f"AI decision brief: {output_dir / str(ai_metadata['brief_file'])}")
            if chat_metadata.get("status") == "ready":
                print(
                    "Chat bridge synced: "
                    f"{chat_metadata['repository']}@{chat_metadata['branch']}:{chat_metadata['pointer_path']}"
                )
                print("Chat use: ask ChatGPT for the latest Dastan recommendation; no file upload is required.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
