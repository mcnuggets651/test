#!/usr/bin/env python3
from __future__ import print_function

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PRIVATE_REPO_SLUG = "mcnuggets651/fpl"
QUERY_FILES = (
    "tools/apex_strategy_query.py",
    "tools/apex_private_query.py",
    "tools/apex_private_query_entry.py",
)
MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)


def git_text(repo, *args):
    p = run(["git", "-C", str(repo)] + list(args))
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:500])
    return p.stdout.strip()


def github_slug(remote):
    value = remote.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    prefixes = ("https://github.com/", "http://github.com/", "ssh://git@github.com/", "git@github.com:")
    for prefix in prefixes:
        if value.lower().startswith(prefix.lower()):
            return value[len(prefix):]
    return None


def find_token():
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return "env"
    gh = shutil.which("gh")
    if gh:
        p = run([gh, "auth", "token"])
        if p.returncode == 0 and p.stdout.strip():
            return "gh"
    return None


def runtime_cache_summary(home, pins):
    runtime = home / "runtime.json"
    upstream = home / "upstream" / "AIrsenal"
    venv_python = home / "venv" / "bin" / "python"
    result = {"present": runtime.is_file(), "candidate_valid": False}
    if not runtime.is_file() or not venv_python.is_file() or not (upstream / ".git").is_dir():
        return result
    try:
        payload = json.loads(runtime.read_text(encoding="utf-8"))
    except Exception:
        result["reason"] = "runtime_json_invalid"
        return result
    keys = {
        "upstream_sha": pins.get("upstream_sha"),
        "upstream_project_version": pins.get("upstream_project_version"),
        "upstream_license_blob_sha": pins.get("upstream_license_blob_sha"),
        "upstream_uv_lock_blob_sha": pins.get("upstream_uv_lock_blob_sha"),
        "uv_version": pins.get("uv_version"),
        "python_version_pin": pins.get("python_version"),
    }
    for key, expected in keys.items():
        if payload.get(key) != expected:
            result["reason"] = "runtime_pin_mismatch:%s" % key
            return result
    p = run([str(venv_python), "-c", "import airsenal,sys; print(airsenal.__version__); print(sys.version.split()[0])"])
    if p.returncode != 0:
        result["reason"] = "venv_import_failed"
        return result
    lines = p.stdout.strip().splitlines()
    if lines[:2] != [str(pins.get("upstream_project_version")), str(pins.get("python_version"))]:
        result["reason"] = "venv_version_mismatch"
        return result
    try:
        if git_text(upstream, "rev-parse", "HEAD") != str(pins.get("upstream_sha")):
            result["reason"] = "upstream_head_mismatch"
            return result
        if git_text(upstream, "status", "--porcelain=v1"):
            result["reason"] = "upstream_dirty"
            return result
    except Exception:
        result["reason"] = "upstream_probe_failed"
        return result
    result["candidate_valid"] = True
    return result


def runner_summary(runner_dir):
    result = {"configured": False, "service_file_present": False, "launchd_loaded": False}
    if runner_dir is None:
        return result
    runner_dir = runner_dir.expanduser().resolve()
    result["runner_dir_present"] = runner_dir.is_dir()
    if not runner_dir.is_dir():
        return result
    service_file = runner_dir / ".service"
    result["service_file_present"] = service_file.is_file()
    if service_file.is_file():
        try:
            plist = Path(service_file.read_text(encoding="utf-8").strip()).expanduser()
            result["plist_present"] = plist.is_file()
            if plist.is_file():
                label = plist.stem
                result["configured"] = True
                p = run(["launchctl", "print", "gui/%s/%s" % (os.getuid(), label)])
                result["launchd_loaded"] = p.returncode == 0
        except Exception:
            pass
    custom = Path.home() / "Library" / "LaunchAgents" / "com.fplapex.private-runner.plist"
    if custom.is_file():
        result["configured"] = True
        result["service_file_present"] = True
        p = run(["launchctl", "print", "gui/%s/com.fplapex.private-runner" % os.getuid()])
        result["launchd_loaded"] = p.returncode == 0
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, default=Path.home() / ".local/share/airsenal-chat")
    ap.add_argument("--private-repo", type=Path)
    ap.add_argument("--pins", type=Path, required=True)
    ap.add_argument("--runner-dir", type=Path, default=Path.home() / "actions-runner-fpl")
    ap.add_argument("--phase", choices=("pre", "post", "status"), default="status")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args(argv)

    pins = json.loads(args.pins.read_text(encoding="utf-8"))
    home = args.home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(str(home))
    checks = {
        "schema": "airsenal-chat-doctor-v1",
        "phase": args.phase,
        "platform": platform.system(),
        "machine": platform.machine(),
        "free_bytes": usage.free,
        "disk_ok": usage.free >= MIN_FREE_BYTES,
        "git_available": bool(shutil.which("git")),
        "github_auth_source": find_token(),
        "runtime_cache": runtime_cache_summary(home, pins),
        "runner": runner_summary(args.runner_dir),
    }
    errors = []
    if not checks["disk_ok"]:
        errors.append("less than 5 GiB free under AIrsenal home")
    if not checks["git_available"]:
        errors.append("git is unavailable")
    if args.phase in ("pre", "post") and not checks["github_auth_source"]:
        errors.append("GitHub authentication unavailable (GITHUB_TOKEN or gh auth required)")
    if platform.system() == "Darwin" and platform.machine() != "arm64":
        errors.append("local macOS runtime must be ARM64")

    if args.private_repo is not None:
        repo = args.private_repo.expanduser().resolve()
        checks["private_repo"] = {"path_present": repo.is_dir(), "valid": False}
        if not repo.is_dir():
            errors.append("private repo does not exist")
        else:
            try:
                top = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
                remote = git_text(repo, "remote", "get-url", "origin")
                missing = [rel for rel in QUERY_FILES if not (repo / rel).is_file()]
                dirty = git_text(repo, "status", "--porcelain=v1", "--", *QUERY_FILES)
                valid = top == repo and (github_slug(remote) or "").lower() == PRIVATE_REPO_SLUG and not missing and not dirty
                checks["private_repo"].update({"valid": valid, "query_files_missing": len(missing), "query_files_clean": not bool(dirty)})
                if not valid:
                    errors.append("private repo/query boundary failed validation")
            except Exception as exc:
                errors.append("private repo validation failed: %s" % exc)

    if args.phase == "post" and not checks["runtime_cache"].get("candidate_valid"):
        errors.append("post-bootstrap runtime cache verification failed")

    checks["ok"] = not errors
    checks["errors"] = errors
    text = json.dumps(checks, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, encoding="utf-8")
        try:
            args.json_out.chmod(0o600)
        except OSError:
            pass
    print(text, end="")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
