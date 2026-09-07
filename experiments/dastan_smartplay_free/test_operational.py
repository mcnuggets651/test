from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import operational


class OperationalTests(unittest.TestCase):
    def test_github_repo_slug(self) -> None:
        for remote in (
            "https://github.com/mcnuggets651/fpl.git",
            "git@github.com:mcnuggets651/fpl.git",
            "ssh://git@github.com/mcnuggets651/fpl.git",
        ):
            self.assertEqual(operational.github_repo_slug(remote), "mcnuggets651/fpl")
        self.assertIsNone(operational.github_repo_slug("https://example.com/mcnuggets651/fpl.git"))

    def test_snapshot_identity_requires_private_attestation(self) -> None:
        payload = {
            "run": {"attestation_scope": "PUBLIC_CANONICAL", "immutable": True, "target_gameweek": 4},
            "team_state": {"entry_id": 63984, "state_complete_for_transfers": True},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PRIVATE_MANAGER"):
                operational.snapshot_identity(path)

    def test_snapshot_identity(self) -> None:
        payload = {
            "run": {
                "attestation_scope": "PRIVATE_MANAGER",
                "immutable": True,
                "target_gameweek": 4,
                "run_id": "run-1",
                "release_tag": "tag-1",
            },
            "team_state": {"entry_id": 63984, "state_complete_for_transfers": True},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            identity = operational.snapshot_identity(path)
            self.assertEqual(identity["entry_id"], 63984)
            self.assertEqual(identity["gameweek"], 4)
            self.assertEqual(len(identity["snapshot_sha256"]), 64)

    def test_build_strategy_command_preserves_policy(self) -> None:
        command = operational.build_strategy_command(
            root=Path("/x"),
            snapshot=Path("/tmp/snapshot.json"),
            output_dir=Path("/tmp/output"),
            posture="neutral",
            force_refresh=True,
            plan_b=True,
            no_hits=True,
            max_projection_age_hours=3,
        )
        self.assertIn("--apex-strategy-snapshot", command)
        self.assertIn("--force-refresh", command)
        self.assertIn("--plan-b", command)
        self.assertIn("--no-hits", command)
        self.assertEqual(command[command.index("--max-projection-age-hours") + 1], "3")

    def test_validate_strategy_manifest_hash_binding(self) -> None:
        identity = {"entry_id": 63984, "gameweek": 4, "snapshot_sha256": "abc"}
        payload = {
            "schema": operational.STRATEGY_SCHEMA,
            "entry_id": 63984,
            "gameweek": 4,
            "team_state": {"source_sha256": "abc"},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            operational.validate_strategy_manifest(path, identity)
            payload["team_state"]["source_sha256"] = "wrong"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "hash-bound"):
                operational.validate_strategy_manifest(path, identity)

    def test_resolve_token_prefers_environment(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}, clear=False), mock.patch(
            "operational.subprocess.run"
        ) as run:
            self.assertEqual(operational.resolve_github_token(), "secret")
            run.assert_not_called()

    def test_validate_private_repo_rejects_dirty_query_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/mcnuggets651/fpl.git"],
                check=True,
            )
            for relative in operational.QUERY_FILES:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("print(1)\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            (repo / operational.QUERY_FILES[0]).write_text("print(2)\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "local modifications"):
                operational.validate_private_repo(repo)

    def test_private_query_never_echoes_snapshot_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "tools").mkdir()
            script = repo / "tools" / "apex_strategy_query.py"
            script.write_text(
                'import argparse,json,pathlib\n'
                'p=argparse.ArgumentParser(); p.add_argument("--run-id"); p.add_argument("--top-n"); p.add_argument("--output"); a=p.parse_args()\n'
                'print("PRIVATE SQUAD")\n'
                'pathlib.Path(a.output).write_text(json.dumps({"ok":1}))\n',
                encoding="utf-8",
            )
            output = repo / "out.json"
            operational.query_latest_private_snapshot({"path": repo}, "token", output)
            self.assertTrue(output.exists())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
