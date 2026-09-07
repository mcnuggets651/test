from __future__ import annotations

import json
import os
import subprocess
import tempfile
import types
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

    def run_fake_operational(self, base: Path, ai_side_effect=None) -> tuple[int, Path]:
        snapshot_payload = {
            "run": {
                "attestation_scope": "PRIVATE_MANAGER",
                "immutable": True,
                "target_gameweek": 4,
                "run_id": "run-1",
                "release_tag": "tag-1",
                "published_at": "2026-09-07T00:00:00Z",
            },
            "team_state": {"entry_id": 63984, "state_complete_for_transfers": True},
        }
        private_repo = base / "fpl"
        private_repo.mkdir()
        output = base / "state"

        def fake_query(_repo_info: dict, _token: str, snapshot: Path) -> None:
            snapshot.write_text(json.dumps(snapshot_payload), encoding="utf-8")
            os.chmod(snapshot, 0o600)

        def fake_run(command, *args, **kwargs):
            if len(command) > 1 and str(command[1]).endswith("strategy.py"):
                output_dir = Path(command[command.index("--output-dir") + 1])
                snapshot = Path(command[command.index("--apex-strategy-snapshot") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "solution").mkdir(exist_ok=True)
                (output_dir / "solution" / "summary.md").write_text("ok\n", encoding="utf-8")
                manifest = {
                    "schema": operational.STRATEGY_SCHEMA,
                    "entry_id": 63984,
                    "gameweek": 4,
                    "team_state": {"source_sha256": operational.sha256_file(snapshot)},
                }
                (output_dir / "strategy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                return types.SimpleNamespace(returncode=0)
            raise AssertionError(f"unexpected subprocess call: {command}")

        repo_info = {
            "path": private_repo,
            "head": "a" * 40,
            "query_files": {relative: "b" * 64 for relative in operational.QUERY_FILES},
        }
        ai_metadata = {
            "schema": "dastan-smartplay-ai-context-v1",
            "status": "ready",
            "context_file": "ai_decision_context.json",
            "context_sha256": "c" * 64,
            "brief_file": "ai_decision_brief.md",
            "brief_sha256": "d" * 64,
            "checksum_file": "ai_decision_context.sha256",
            "privacy": "PRIVATE_MANAGER_LOCAL_ONLY",
        }
        ai_patch = (
            mock.patch("operational.generate_ai_sidecar", side_effect=ai_side_effect)
            if ai_side_effect is not None
            else mock.patch("operational.generate_ai_sidecar", return_value=ai_metadata)
        )
        with mock.patch("operational.public_repo_root", return_value=None), mock.patch(
            "operational.resolve_private_repo", return_value=private_repo
        ), mock.patch("operational.validate_private_repo", return_value=repo_info), mock.patch(
            "operational.resolve_github_token", return_value="token"
        ), mock.patch("operational.query_latest_private_snapshot", side_effect=fake_query), mock.patch(
            "operational.subprocess.run", side_effect=fake_run
        ), ai_patch:
            result = operational.main(["--private-repo", str(private_repo), "--output-dir", str(output)])
        return result, output

    def test_main_orchestrates_private_query_to_ai_bound_operational_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, output = self.run_fake_operational(Path(temp))
            self.assertEqual(result, 0)
            op_manifest = json.loads((output / "operational_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(op_manifest["schema"], operational.OPERATIONAL_SCHEMA)
            self.assertEqual(op_manifest["entry_id"], 63984)
            self.assertEqual(op_manifest["ai_decision_context"]["status"], "ready")
            self.assertEqual(op_manifest["ai_decision_context"]["context_sha256"], "c" * 64)
            self.assertFalse(op_manifest["operational_contract"]["private_snapshot_retained"])
            self.assertFalse(op_manifest["operational_contract"]["ai_failure_invalidates_core_model"])
            self.assertFalse((output / "strategy_snapshot.json").exists())

    def test_ai_sidecar_failure_never_invalidates_core_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, output = self.run_fake_operational(Path(temp), ai_side_effect=ValueError("sidecar broken"))
            self.assertEqual(result, 0)
            op_manifest = json.loads((output / "operational_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(op_manifest["ai_decision_context"]["status"], "failed")
            self.assertTrue(op_manifest["ai_decision_context"]["core_model_result_valid"])
            self.assertEqual((output / "solution" / "summary.md").read_text(encoding="utf-8"), "ok\n")


if __name__ == "__main__":
    unittest.main()
