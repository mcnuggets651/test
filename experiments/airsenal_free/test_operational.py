from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import operational


class OperationalTests(unittest.TestCase):
    def test_runtime_env_isolated_and_strips_credentials(self):
        with patch.dict(
            operational.os.environ,
            {"FPL_LOGIN": "x", "FPL_PASSWORD": "y", "DISCORD_WEBHOOK": "z"},
            clear=False,
        ):
            env = operational.safe_runtime_env(
                Path("/tmp/isolated-home"),
                Path("/tmp/isolated-home/data.db"),
                63984,
            )
        self.assertEqual(env["AIRSENAL_HOME"], "/tmp/isolated-home")
        self.assertEqual(env["AIRSENAL_DB_FILE"], "/tmp/isolated-home/data.db")
        self.assertEqual(env["FPL_TEAM_ID"], "63984")
        self.assertNotIn("FPL_LOGIN", env)
        self.assertNotIn("FPL_PASSWORD", env)
        self.assertNotIn("DISCORD_WEBHOOK", env)

    def test_h3_and_h5_result_validation_and_hash_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "base.db"
            db.write_bytes(b"base-db")
            sha = operational.sha256_file(db)
            for horizon in (3, 5):
                path = root / f"h{horizon}.json"
                gws = list(range(4, 4 + horizon))
                payload = {
                    "schema": operational.HORIZON_SCHEMA,
                    "horizon": horizon,
                    "entry_id": 63984,
                    "target_gameweek": 4,
                    "gameweeks": gws,
                    "db": {"pre_prediction_sha256": sha},
                    "optimizer": {
                        "strategy": {
                            "expected_points_by_gameweek": {
                                str(gw): 50.0 for gw in gws
                            }
                        },
                        "starting_price_reconciliation": [
                            {"match": True} for _ in range(15)
                        ],
                    },
                }
                path.write_text(json.dumps(payload), encoding="utf-8")
                out = operational.validate_horizon_result(
                    path,
                    horizon=horizon,
                    entry_id=63984,
                    target_gw=4,
                    base_db_sha256=sha,
                )
                self.assertEqual(out["horizon"], horizon)

    def test_stale_horizon_result_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "h3.json"
            gws = [4, 5, 6]
            path.write_text(
                json.dumps(
                    {
                        "schema": operational.HORIZON_SCHEMA,
                        "horizon": 3,
                        "entry_id": 63984,
                        "target_gameweek": 3,
                        "gameweeks": gws,
                        "db": {"pre_prediction_sha256": "x"},
                        "optimizer": {
                            "strategy": {
                                "expected_points_by_gameweek": {
                                    str(gw): 1 for gw in gws
                                }
                            },
                            "starting_price_reconciliation": [
                                {"match": True} for _ in range(15)
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(operational.OperationalError):
                operational.validate_horizon_result(
                    path,
                    horizon=3,
                    entry_id=63984,
                    target_gw=4,
                    base_db_sha256="x",
                )

    def test_price_reconciliation_requires_exact_15(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "h3.json"
            gws = [4, 5, 6]
            path.write_text(
                json.dumps(
                    {
                        "schema": operational.HORIZON_SCHEMA,
                        "horizon": 3,
                        "entry_id": 63984,
                        "target_gameweek": 4,
                        "gameweeks": gws,
                        "db": {"pre_prediction_sha256": "x"},
                        "optimizer": {
                            "strategy": {
                                "expected_points_by_gameweek": {
                                    str(gw): 1 for gw in gws
                                }
                            },
                            "starting_price_reconciliation": [
                                {"match": True} for _ in range(14)
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(operational.OperationalError):
                operational.validate_horizon_result(
                    path,
                    horizon=3,
                    entry_id=63984,
                    target_gw=4,
                    base_db_sha256="x",
                )

    def test_publish_failure_isolated_from_calculation(self):
        def fail_publish(**_kwargs):
            raise RuntimeError("push failed")

        bad_publish = types.SimpleNamespace(publish=fail_publish)
        good_verify = types.SimpleNamespace(verify=lambda **_kwargs: {"status": "ready"})
        with patch.dict(
            sys.modules,
            {
                "publish_chat_bridge": bad_publish,
                "verify_chat_bridge": good_verify,
            },
        ):
            result = operational.attempt_chat_publish(
                run_dir=Path("/tmp/run"),
                private_repo=Path("/tmp/private"),
                no_chat_publish=False,
            )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["calculation_still_valid"])

    def test_no_chat_publish_is_explicit(self):
        result = operational.attempt_chat_publish(
            run_dir=Path("/tmp/run"),
            private_repo=Path("/tmp/private"),
            no_chat_publish=True,
        )
        self.assertEqual(
            result,
            {"status": "disabled", "reason": "--no-chat-publish"},
        )

    def test_context_preserves_layers_and_provenance_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "h3").mkdir()
            (root / "h5").mkdir()
            (root / "owner_state.json").write_text("{}", encoding="utf-8")
            (root / "official_fpl_provenance.json").write_text("{}", encoding="utf-8")
            h3 = {"db": {"pre_prediction_sha256": "base"}}
            h5 = {"db": {"pre_prediction_sha256": "base"}}
            (root / "h3/result.json").write_text(json.dumps(h3), encoding="utf-8")
            (root / "h5/result.json").write_text(json.dumps(h5), encoding="utf-8")
            owner = {
                "entry_id": 63984,
                "target_gameweek": 4,
                "bank_tenths": 3,
                "free_transfers": 2,
                "active_chip": None,
                "squad": [],
                "private_authority": {"raw_snapshot_sha256": "raw"},
            }
            context = operational.build_context(
                run_timestamp="2026-09-07T12:00:00+00:00",
                owner=owner,
                official_manifest={"target_gameweek": 4},
                repo_info={"head": "a" * 40, "file_sha256": {}},
                private_info={"head": "b" * 40, "query_file_sha256": {}},
                runtime_pin={"upstream_sha": "c" * 40},
                pins={
                    "upstream_repository": "alan-turing-institute/AIrsenal",
                    "upstream_sha": "c" * 40,
                    "upstream_license": "MIT",
                    "upstream_uv_lock_blob_sha": "d" * 40,
                },
                base_db_sha256="base",
                h3=h3,
                h5=h5,
                run_dir=root,
                scenario_path=None,
            )
            self.assertEqual(set(context["airsenal_result"]), {"h3", "h5"})
            self.assertEqual(context["external_evidence"], [])
            self.assertIsNone(context["ai_interpretation"])
            self.assertIsNone(context["final_recommendation"])
            self.assertTrue(
                context["interpretation_contract"][
                    "unsolved_counterfactual_numerical_edges_forbidden"
                ]
            )
            self.assertFalse(context["privacy"]["safe_for_public_artifact_upload"])
            for rel, digest in context["integrity"]["files"].items():
                self.assertEqual(
                    digest,
                    hashlib.sha256((root / rel).read_bytes()).hexdigest(),
                )

    def test_run_directory_is_immutable_unique_shape(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            owner = {
                "entry_id": 63984,
                "target_gameweek": 4,
                "private_authority": {"raw_snapshot_sha256": "a" * 64},
            }
            now = operational.dt.datetime(
                2026,
                9,
                7,
                12,
                0,
                0,
                123456,
                tzinfo=operational.dt.timezone.utc,
            )
            path, run_id = operational.create_run_dir(
                home,
                owner,
                "b" * 64,
                now=now,
            )
            self.assertIn("entry-63984/gw4", str(path))
            self.assertIn("20260907T120000123456Z", run_id)
            with self.assertRaises(FileExistsError):
                path.mkdir(parents=True, exist_ok=False)


if __name__ == "__main__":
    unittest.main()
