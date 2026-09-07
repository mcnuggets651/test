from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import publish_chat_bridge as bridge


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class PublishChatBridgeTests(unittest.TestCase):
    def make_state(self, base: Path) -> Path:
        state = base / "state"
        state.mkdir()
        strategy = {
            "schema": "dastan-smartplay-free-strategy-v2",
            "entry_id": 63984,
            "gameweek": 4,
        }
        strategy_path = state / "strategy_manifest.json"
        strategy_path.write_text(json.dumps(strategy) + "\n", encoding="utf-8")
        strategy_sha = hashlib.sha256(strategy_path.read_bytes()).hexdigest()
        context = {
            "schema": bridge.AI_CONTEXT_SCHEMA,
            "generated_at": "2026-09-07T11:30:00+00:00",
            "privacy": {
                "classification": "PRIVATE_MANAGER_LOCAL_ONLY",
                "contains_exact_owner_state": True,
                "safe_for_public_artifact_upload": False,
            },
            "scope": {
                "entry_id": 63984,
                "gameweek": 4,
                "projection_horizon": 1,
                "model": "open Dastan",
                "optimizer": "open SmartPlay Solver",
            },
            "private_authority": {
                "run_id": "manager-1",
                "release_tag": "private-tag-1",
                "immutable": True,
            },
            "integrity": {"strategy_manifest_sha256": strategy_sha},
            "owner_state": {"free_transfers": 2, "bank_tenths": 5},
        }
        context_path = state / "ai_decision_context.json"
        context_path.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
        context_sha = hashlib.sha256(context_path.read_bytes()).hexdigest()
        (state / "ai_decision_context.sha256").write_text(
            f"{context_sha}  ai_decision_context.json\n", encoding="utf-8"
        )
        (state / "ai_decision_brief.md").write_text("# private brief\n", encoding="utf-8")
        return state

    def make_remote(self, base: Path, *, configure_private_author: bool = True) -> Path:
        bare = base / "remote.git"
        run("git", "init", "--bare", str(bare))
        seed = base / "seed"
        run("git", "clone", str(bare), str(seed))
        run("git", "config", "user.email", "test@example.com", cwd=seed)
        run("git", "config", "user.name", "Test", cwd=seed)
        (seed / "README.md").write_text("private repo\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=seed)
        run("git", "commit", "-m", "seed", cwd=seed)
        run("git", "branch", "-M", "main", cwd=seed)
        run("git", "push", "-u", "origin", "main", cwd=seed)
        run("git", "branch", bridge.RESULTS_BRANCH, cwd=seed)
        run("git", "push", "origin", bridge.RESULTS_BRANCH, cwd=seed)

        private = base / "private"
        run("git", "clone", str(bare), str(private))
        if configure_private_author:
            run("git", "config", "user.email", "test@example.com", cwd=private)
            run("git", "config", "user.name", "Test", cwd=private)
        return private

    def test_validate_bundle_rejects_bad_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(Path(temp))
            (state / "ai_decision_context.sha256").write_text("0" * 64 + "  x\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                bridge.validate_local_bundle(state)

    def test_validate_bundle_rejects_public_safe_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(Path(temp))
            context_path = state / "ai_decision_context.json"
            payload = json.loads(context_path.read_text(encoding="utf-8"))
            payload["privacy"]["safe_for_public_artifact_upload"] = True
            context_path.write_text(json.dumps(payload), encoding="utf-8")
            digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
            (state / "ai_decision_context.sha256").write_text(f"{digest}  ai_decision_context.json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsafe for public"):
                bridge.validate_local_bundle(state)

    def test_missing_results_branch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = self.make_state(base)
            private = self.make_remote(base)
            run("git", "push", "origin", "--delete", bridge.RESULTS_BRANCH, cwd=private)
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                bridge.publish(
                    state_dir=state,
                    private_repo=private,
                    require_github_origin=False,
                    script_dir=private,
                )

    def test_full_publish_creates_immutable_run_then_latest_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = self.make_state(base)
            private = self.make_remote(base, configure_private_author=False)
            self.assertEqual(run("git", "status", "--porcelain", cwd=private), "")

            result = bridge.publish(
                state_dir=state,
                private_repo=private,
                require_github_origin=False,
                script_dir=private,
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["entry_id"], 63984)
            self.assertEqual(result["gameweek"], 4)
            self.assertEqual(len(result["run_commit_sha"]), 40)
            self.assertEqual(len(result["pointer_commit_sha"]), 40)
            self.assertNotEqual(result["run_commit_sha"], result["pointer_commit_sha"])
            self.assertEqual(run("git", "status", "--porcelain", cwd=private), "")

            run("git", "fetch", "origin", bridge.RESULTS_BRANCH, cwd=private)
            pointer_raw = run(
                "git",
                "show",
                f"origin/{bridge.RESULTS_BRANCH}:{result['pointer_path']}",
                cwd=private,
            )
            pointer = json.loads(pointer_raw)
            self.assertEqual(pointer["schema"], bridge.POINTER_SCHEMA)
            self.assertEqual(pointer["run_commit_sha"], result["run_commit_sha"])
            self.assertEqual(pointer["context_sha256"], result["context_sha256"])

            committed_context = run(
                "git",
                "show",
                f"{result['run_commit_sha']}:{result['context_path']}",
                cwd=private,
            )
            self.assertEqual(
                hashlib.sha256((committed_context + "\n").encode("utf-8")).hexdigest(),
                result["context_sha256"],
            )

            author = run(
                "git",
                "show",
                "-s",
                "--format=%an <%ae>",
                result["run_commit_sha"],
                cwd=private,
            )
            self.assertEqual(author, "Dastan Local Bridge <dastan-local@users.noreply.github.com>")

            with self.assertRaisesRegex(RuntimeError, "immutable run path already exists"):
                bridge.publish(
                    state_dir=state,
                    private_repo=private,
                    require_github_origin=False,
                    script_dir=private,
                )


if __name__ == "__main__":
    unittest.main()
