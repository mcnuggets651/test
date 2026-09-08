from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish_staged_bridge
import verify_staged_bridge

ROOT = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class StagedProducerTests(unittest.TestCase):
    def test_staged_bridge_publishes_independently_and_verifies_exact_commits(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = base / "remote.git"
            repo = base / "work"
            source = base / "source"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            git(repo, "config", "user.name", "Test")
            git(repo, "config", "user.email", "test@example.invalid")
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            git(repo, "add", "seed.txt")
            git(repo, "commit", "-m", "seed")
            git(repo, "branch", "-M", "main")
            git(repo, "remote", "add", "origin", str(remote))
            git(repo, "push", "-u", "origin", "main")
            git(repo, "branch", "airsenal-results")
            git(repo, "push", "origin", "airsenal-results")

            owner = source / "owner_state.json"
            official = source / "official_fpl_provenance.json"
            scenario = source / "scenario.json"
            write_json(owner, {"schema": "airsenal-chat-owner-state-v1", "entry_id": 63984})
            write_json(official, {"schema": "airsenal-chat-official-fpl-v1", "target_gameweek": 4})
            write_json(scenario, {"max_total_hit": 0})
            forecast = source / "forecast.json"
            h3 = source / "h3.json"
            h5 = source / "h5.json"
            write_json(
                forecast,
                {
                    "schema": "airsenal-chat-prediction-stage-v1",
                    "generated_at": "2026-09-08T05:00:00+00:00",
                    "horizon": 5,
                    "entry_id": 63984,
                    "target_gameweek": 4,
                    "gameweeks": [4, 5, 6, 7, 8],
                    "players": [{"element_id": 1}],
                },
            )
            for path, horizon in ((h3, 3), (h5, 5)):
                write_json(
                    path,
                    {
                        "schema": "airsenal-chat-horizon-result-v1",
                        "generated_at": "2026-09-08T06:00:00+00:00",
                        "horizon": horizon,
                        "entry_id": 63984,
                        "target_gameweek": 4,
                    },
                )

            original_validate = publish_staged_bridge.legacy.validate_private_repo
            with patch.object(
                publish_staged_bridge.legacy,
                "validate_private_repo",
                side_effect=lambda path: original_validate(path, require_github_origin=False),
            ):
                init = publish_staged_bridge.initialize(
                    private_repo=repo,
                    entry_id=63984,
                    target_gw=4,
                    producer_run_id="producer-1",
                    owner_state_path=owner,
                    official_path=official,
                    experiment_sha="a" * 40,
                    upstream_sha="b" * 40,
                    scenario_path=scenario,
                )
                self.assertEqual(init["status"], "running")
                for stage, artifact in (("forecast", forecast), ("h3", h3), ("h5", h5)):
                    result = publish_staged_bridge.publish_stage(
                        private_repo=repo,
                        stage=stage,
                        artifact_path=artifact,
                        owner_state_path=owner,
                        official_path=official,
                        producer_run_id="producer-1",
                        experiment_sha="a" * 40,
                        upstream_sha="b" * 40,
                        scenario_path=scenario,
                    )
                    self.assertEqual(result["status"], "ready")
                    self.assertEqual(result["stage"], stage)

            with patch.object(
                verify_staged_bridge.legacy_verify,
                "github_slug",
                return_value=verify_staged_bridge.PRIVATE_REPO_SLUG,
            ):
                verified = verify_staged_bridge.verify(
                    private_repo=repo,
                    entry_id=63984,
                    verify_combined=False,
                )
            self.assertEqual(verified["target_gameweek"], 4)
            self.assertTrue(verified["exact_commit_retrieval_verified"])
            self.assertEqual(
                {stage: row["status"] for stage, row in verified["stages"].items()},
                {"forecast": "ready", "h3": "ready", "h5": "ready"},
            )

    def test_forecast_is_material_model_evidence_not_only_a_tag(self):
        body = (ROOT / "prediction_worker.py").read_text(encoding="utf-8")
        for marker in (
            '"players": players',
            '"expected_points": gw_points',
            '"recent_minutes_basis": recent_minutes',
            '"explicit_expected_minutes": None',
        ):
            self.assertIn(marker, body)

    def test_daily_producer_publishes_forecast_before_h3_before_h5(self):
        body = (ROOT / "daily_producer.py").read_text(encoding="utf-8")
        forecast_pos = body.index('stage="forecast"')
        h3_pos = body.index('stage="h3"')
        h5_pos = body.index('stage="h5"')
        self.assertLess(forecast_pos, h3_pos)
        self.assertLess(h3_pos, h5_pos)
        self.assertIn("mark_combined_ready", body)
        self.assertIn("verify_staged_bridge.verify", body)
        self.assertNotIn("transfer_my_team", body)
        self.assertNotIn("make_transfers", body)

    def test_producer_launcher_uses_isolated_runtime(self):
        body = (ROOT / "produce.sh").read_text(encoding="utf-8")
        self.assertIn("BOOTSTRAP_FAILED", body)
        self.assertIn("DOCTOR_PRE_FAILED", body)
        self.assertIn("DOCTOR_POST_FAILED", body)
        self.assertIn("daily_producer.py", body)
        self.assertIn('"$CHAT_HOME/venv/bin/python"', body)


if __name__ == "__main__":
    unittest.main()
