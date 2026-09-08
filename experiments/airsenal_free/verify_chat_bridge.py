#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

PRIVATE_REPO_SLUG = "mcnuggets651/fpl"
RESULTS_BRANCH = "airsenal-results"
POINTER_SCHEMA = "airsenal-chat-bridge-latest-v1"
CONTEXT_SCHEMA = "airsenal-chat-decision-context-v1"


class VerifyError(RuntimeError):
    pass


def git(
    repo: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
    )
    if check and result.returncode != 0:
        err = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        out = result.stdout if text else result.stdout.decode("utf-8", errors="replace")
        raise VerifyError(f"git {' '.join(args)} failed: {(err or out).strip()[:500]}")
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


def parse_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifyError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise VerifyError(f"{label} must be a JSON object")
    return value


def show_bytes(repo: Path, ref: str, path: str) -> bytes:
    result = git(repo, "show", f"{ref}:{path}", text=False)
    return bytes(result.stdout)


def verify(
    *,
    private_repo: Path,
    entry_id: int = 63984,
    branch: str = RESULTS_BRANCH,
    require_github_origin: bool = True,
) -> dict[str, Any]:
    repo = private_repo.expanduser().resolve()
    if not repo.is_dir():
        raise VerifyError(f"private repository does not exist: {repo}")
    top = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise VerifyError(f"private repository must be its Git toplevel: {top}")
    if require_github_origin:
        remote = git_text(repo, "remote", "get-url", "origin")
        if (github_slug(remote) or "").lower() != PRIVATE_REPO_SLUG.lower():
            raise VerifyError(f"private repository origin must be {PRIVATE_REPO_SLUG}")
    git(repo, "fetch", "--quiet", "origin", branch)
    branch_ref = f"origin/{branch}"
    pointer_path = f"airsenal/latest/entry-{int(entry_id)}.json"
    pointer_bytes = show_bytes(repo, branch_ref, pointer_path)
    pointer = parse_json_bytes(pointer_bytes, "latest pointer")
    required = {
        "schema": POINTER_SCHEMA,
        "status": "ready",
        "repository": PRIVATE_REPO_SLUG,
        "branch": branch,
        "entry_id": int(entry_id),
    }
    for key, expected in required.items():
        if pointer.get(key) != expected:
            raise VerifyError(
                f"latest pointer {key} mismatch: {pointer.get(key)!r} != {expected!r}"
            )
    run_commit = str(pointer.get("run_commit_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", run_commit):
        raise VerifyError("latest pointer has invalid run_commit_sha")
    ancestor = git(repo, "merge-base", "--is-ancestor", run_commit, branch_ref, check=False)
    if ancestor.returncode != 0:
        raise VerifyError("pointer run commit is not an ancestor of results branch head")
    context_path = str(pointer.get("context_path") or "")
    if not context_path.startswith(f"airsenal/runs/entry-{entry_id}/"):
        raise VerifyError("pointer context_path is outside immutable AIrsenal run root")
    context_bytes = show_bytes(repo, run_commit, context_path)
    context_sha = hashlib.sha256(context_bytes).hexdigest()
    if context_sha != pointer.get("context_sha256"):
        raise VerifyError("exact-commit decision context hash does not match pointer")
    context = parse_json_bytes(context_bytes, "decision context")
    if context.get("schema") != CONTEXT_SCHEMA:
        raise VerifyError("decision context schema mismatch")
    if int(context.get("entry_id") or 0) != int(entry_id):
        raise VerifyError("decision context entry mismatch")
    if int(context.get("target_gameweek") or 0) != int(pointer.get("target_gameweek") or 0):
        raise VerifyError("decision context Gameweek mismatch")
    privacy = context.get("privacy") or {}
    if (
        privacy.get("classification") != "PRIVATE_MANAGER_LOCAL_ONLY"
        or privacy.get("safe_for_public_artifact_upload") is not False
    ):
        raise VerifyError("decision context privacy declaration invalid")
    horizons = context.get("airsenal_result") or {}
    if set(horizons) != {"h3", "h5"}:
        raise VerifyError("decision context does not contain exactly H3 and H5")
    files = (context.get("integrity") or {}).get("files") or {}
    run_root = context_path.rsplit("/", 1)[0]
    for rel, expected_sha in files.items():
        data = show_bytes(repo, run_commit, f"{run_root}/{rel}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha:
            raise VerifyError(f"exact-commit integrity mismatch for {rel}")
    branch_head = git_text(repo, "rev-parse", branch_ref)
    return {
        "status": "ready",
        "repository": PRIVATE_REPO_SLUG,
        "branch": branch,
        "branch_head_sha": branch_head,
        "pointer_path": pointer_path,
        "pointer_commit_sha": branch_head,
        "run_commit_sha": run_commit,
        "run_id": pointer.get("run_id"),
        "context_path": context_path,
        "context_sha256": context_sha,
        "entry_id": int(entry_id),
        "target_gameweek": int(pointer.get("target_gameweek") or 0),
        "horizons": [3, 5],
        "exact_commit_retrieval_verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify private AIrsenal latest pointer and exact immutable run commit"
    )
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument("--entry-id", type=int, default=63984)
    parser.add_argument("--branch", default=RESULTS_BRANCH)
    args = parser.parse_args(argv)
    try:
        result = verify(
            private_repo=args.private_repo,
            entry_id=args.entry_id,
            branch=args.branch,
        )
    except (VerifyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
