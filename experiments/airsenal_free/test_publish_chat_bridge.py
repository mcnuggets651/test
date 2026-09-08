from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import publish_chat_bridge
import verify_chat_bridge


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_bundle(root: Path) -> str:
    (root / "h3").mkdir(parents=True)
    (root / "h5").mkdir(parents=True)
    write_json(root / "owner_state.json", {"schema": "airsenal-chat-owner-state-v1", "entry_id": 63984})
    write_json(
        root / "official_fpl_provenance.json",
        {"schema": "airsenal-chat-official-fpl-v1", "target_gameweek": 4},
    )
    write_json(
        root / "h3/result.json",
        {"schema": "airsenal-chat-horizon-result-v1", "horizon": 3},
    )
    write_json(
        root / "h5/result.json",
        {"schema": "airsenal-chat-horizon-result-v1", "horizon": 5},
    )
    files = {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in (
            "owner_state.json",
            "official_fpl_provenance.json",
            "h3/result.json",
            "h5/result.json",
        )
    }
    context = {
        "schema": "airsenal-chat-decision-context-v1",
        "run_timestamp": "2026-09-07T12:00:00+00:00",
        "entry_id": 63984,
        "target_gameweek": 4,
        "airsenal_result": {"h3": {}, "h5": {}},
        "integrity": {"files": files},
        "privacy": {
            "classification": "PRIVATE_MANAGER_LOCAL_ONLY",
            "safe_for_public_artifact_upload": False,
        },
        "provenance": {
            "experiment_code_sha": "a" * 40,
            "airsenal_upstream_sha": "b" * 40,
        },
        "private_authority": {"run_id": "r"},
    }
    write_json(root / "decision_context.json", context)
    sha = hashlib.sha256((root / "decision_context.json").read_bytes()).hexdigest()
    (root / "decision_context.sha256").write_text(
        f"{sha}  decision_context.json\n",
        encoding="utf-8",
    )
    write_json(
        root / "run_manifest.json",
        {"schema": "airsenal-chat-local-run-v1", "status": "ready"},
    )
    return sha


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote = base / "remote.git"
        self.repo = base / "work"
        self.bundle = base / "bundle"
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", str(self.repo)],
            check=True,
            capture_output=True,
        )
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        git(self.repo, "add", "seed.txt")
        git(self.repo, "commit", "-m", "seed")
        git(self.repo, "branch", "-M", "main")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")
        git(self.repo, "branch", "airsenal-results")
        git(self.repo, "push", "origin", "airsenal-results")
        (self.repo / "local-untracked.txt").write_text("keep me\n", encoding="utf-8")
        self.before = git(
            self.repo,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        make_bundle(self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_two_commit_pointer_and_exact_commit_retrieval(self):
        result = publish_chat_bridge.publish(
            run_dir=self.bundle,
            private_repo=self.repo,
            require_github_origin=False,
        )
        self.assertEqual(result["status"], "ready")
        self.assertNotEqual(result["run_commit_sha"], result["pointer_commit_sha"])
        after = git(
            self.repo,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        self.assertEqual(after, self.before)
        verified = verify_chat_bridge.verify(
            private_repo=self.repo,
            entry_id=63984,
            require_github_origin=False,
        )
        self.assertTrue(verified["exact_commit_retrieval_verified"])
        self.assertEqual(verified["run_commit_sha"], result["run_commit_sha"])
        self.assertEqual(verified["context_sha256"], result["context_sha256"])

    def test_immutable_run_refusal(self):
        publish_chat_bridge.publish(
            run_dir=self.bundle,
            private_repo=self.repo,
            require_github_origin=False,
        )
        with self.assertRaises(publish_chat_bridge.PublishError):
            publish_chat_bridge.publish(
                run_dir=self.bundle,
                private_repo=self.repo,
                require_github_origin=False,
            )

    def test_missing_results_branch_fails_closed(self):
        git(self.repo, "push", "origin", "--delete", "airsenal-results")
        with self.assertRaises(publish_chat_bridge.PublishError):
            publish_chat_bridge.publish(
                run_dir=self.bundle,
                private_repo=self.repo,
                require_github_origin=False,
            )


if __name__ == "__main__":
    unittest.main()
