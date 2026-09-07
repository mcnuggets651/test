from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import strategy


def apex_snapshot() -> dict:
    squad = []
    for index in range(15):
        squad.append(
            {
                "element_id": 100 + index,
                "purchase_price_tenths": 45 + index,
                "selling_price_tenths": 45 + index,
            }
        )
    return {
        "run": {
            "attestation_scope": "PRIVATE_MANAGER",
            "immutable": True,
            "run_id": "run-1",
            "release_tag": "tag-1",
            "published_at": "2026-09-07T00:00:00Z",
        },
        "team_state": {
            "entry_id": 63984,
            "published_gw": 3,
            "bank_tenths": 5,
            "free_transfers": 2,
            "state_complete_for_transfers": True,
            "squad": squad,
        },
    }


class StrategyStateTests(unittest.TestCase):
    def test_apex_snapshot_converts_to_exact_solver_team(self) -> None:
        team, provenance = strategy.solver_team_from_apex_snapshot(apex_snapshot())
        self.assertEqual(len(team["picks"]), 15)
        self.assertEqual(len({p["element"] for p in team["picks"]}), 15)
        self.assertEqual(team["transfers"]["bank"], 5)
        self.assertEqual(team["transfers"]["limit"], 2)
        self.assertEqual(team["transfers"]["value"], sum(45 + i for i in range(15)))
        self.assertEqual(provenance["mode"], "apex_private_strategy_snapshot")
        self.assertEqual(provenance["entry_id"], 63984)
        self.assertNotIn("squad", provenance)

    def test_apex_snapshot_fails_closed_without_private_attestation(self) -> None:
        payload = apex_snapshot()
        payload["run"]["attestation_scope"] = "PUBLIC_CANONICAL"
        with self.assertRaisesRegex(ValueError, "PRIVATE_MANAGER"):
            strategy.solver_team_from_apex_snapshot(payload)

    def test_apex_snapshot_fails_closed_on_incomplete_transfer_state(self) -> None:
        payload = apex_snapshot()
        payload["team_state"]["state_complete_for_transfers"] = False
        with self.assertRaisesRegex(ValueError, "complete for transfers"):
            strategy.solver_team_from_apex_snapshot(payload)

    def test_solver_team_rejects_duplicate_elements(self) -> None:
        team, _ = strategy.solver_team_from_apex_snapshot(apex_snapshot())
        team["picks"][1]["element"] = team["picks"][0]["element"]
        with self.assertRaisesRegex(ValueError, "15 unique"):
            strategy.validate_solver_team(team)

    def test_private_solve_command_has_no_public_state_overrides(self) -> None:
        command = strategy.build_solve_command(
            solver="smartplay-solver",
            solver_csv=Path("projection.csv"),
            solution_dir=Path("solution"),
            gameweek=4,
            posture="neutral",
            team_path=Path("/tmp/team.json"),
            entry_id=None,
            free_transfers=None,
            bank=None,
            plan_b=False,
            no_hits=True,
        )
        self.assertIn("--team", command)
        self.assertNotIn("--entry-id", command)
        self.assertNotIn("--free-transfers", command)
        self.assertNotIn("--bank", command)
        self.assertIn("--no-hits", command)

    def test_public_mode_requires_explicit_current_state_ack(self) -> None:
        args = argparse.Namespace(
            apex_strategy_snapshot=None,
            team_file=None,
            entry_id=63984,
            public_state_is_current=False,
            free_transfers=2,
            bank=0.5,
        )
        with self.assertRaisesRegex(ValueError, "public --entry-id state can hide transfers"):
            strategy.resolve_team_source(args)

    def test_public_mode_requires_ft_and_bank(self) -> None:
        args = argparse.Namespace(
            apex_strategy_snapshot=None,
            team_file=None,
            entry_id=63984,
            public_state_is_current=True,
            free_transfers=None,
            bank=0.5,
        )
        with self.assertRaisesRegex(ValueError, "requires both"):
            strategy.resolve_team_source(args)

    def test_native_team_file_is_hash_bound(self) -> None:
        team, _ = strategy.solver_team_from_apex_snapshot(apex_snapshot())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "team.json"
            path.write_text(json.dumps(team), encoding="utf-8")
            args = argparse.Namespace(
                apex_strategy_snapshot=None,
                team_file=path,
                entry_id=None,
                public_state_is_current=False,
                free_transfers=None,
                bank=None,
            )
            loaded, provenance = strategy.resolve_team_source(args)
            self.assertEqual(loaded["transfers"]["limit"], 2)
            self.assertEqual(provenance["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())


class AcceptanceReuseTests(unittest.TestCase):
    def _payload(self, generated_at: str) -> dict:
        return {
            "schema": "dastan-smartplay-free-acceptance-v3",
            "generated_at": generated_at,
            "gameweek": 4,
            "fpl_is_next": True,
            "upstream": {
                "dastan": strategy.DASTAN_SHA,
                "smartplay_solver": strategy.SOLVER_SHA,
                "smartplay_public_mapping": strategy.SMARTPLAY_PUBLIC_SHA,
            },
        }

    def test_future_dated_acceptance_is_not_reusable(self) -> None:
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "acceptance.json"
            path.write_text(json.dumps(self._payload(future.isoformat())), encoding="utf-8")
            self.assertFalse(strategy.reusable_acceptance(path, 4, 6.0))

    def test_fresh_pinned_acceptance_is_reusable(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "acceptance.json"
            path.write_text(json.dumps(self._payload(now.isoformat())), encoding="utf-8")
            self.assertTrue(strategy.reusable_acceptance(path, 4, 6.0))


if __name__ == "__main__":
    unittest.main()
