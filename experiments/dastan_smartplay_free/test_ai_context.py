from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import ai_context


class AiContextTests(unittest.TestCase):
    def make_state(self, base: Path, *, scope: str = "PRIVATE_MANAGER") -> tuple[Path, Path]:
        state = base / "state"
        projections = state / "projections"
        solution_dir = state / "solution"
        projections.mkdir(parents=True)
        solution_dir.mkdir(parents=True)

        squad = []
        for element in range(1, 16):
            squad.append(
                {
                    "element_id": element,
                    "web_name": f"P{element}",
                    "team_name": "Alpha" if element <= 8 else "Beta",
                    "position": "MID" if element >= 6 else "DEF",
                    "purchase_price_tenths": 50 + element,
                    "selling_price_tenths": 50 + element,
                    "current_price_tenths_at_snapshot": 51 + element,
                    "private_note": "MUST_NOT_LEAK",
                }
            )
        snapshot_payload = {
            "run": {
                "attestation_scope": scope,
                "immutable": True,
                "target_gameweek": 4,
                "run_id": "run-1",
                "release_tag": "private-tag",
                "published_at": "2026-09-07T00:00:00Z",
            },
            "team_state": {
                "entry_id": 63984,
                "published_gw": 3,
                "bank_tenths": 5,
                "free_transfers": 2,
                "active_chip": None,
                "state_complete_for_transfers": True,
                "squad": squad,
            },
            "token": "TOP_LEVEL_SECRET_MUST_NOT_LEAK",
        }
        snapshot = base / "snapshot.json"
        snapshot.write_text(json.dumps(snapshot_payload), encoding="utf-8")

        solver_path = projections / "dastan_gw4_solver.csv"
        with solver_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["element", "gameweek", "xpts", "expected_minutes"])
            writer.writeheader()
            for element in range(1, 17):
                writer.writerow(
                    {
                        "element": element,
                        "gameweek": 4,
                        "xpts": round(2.0 + element / 10.0, 2),
                        "expected_minutes": 80 if element != 15 else 20,
                    }
                )

        fixtures_path = projections / "dastan_gw4_fixtures.csv"
        fixture_fields = [
            "element",
            "fpl_code",
            "player_name",
            "team_name",
            "position",
            "gameweek",
            "fixture",
            "kickoff_time",
            "xpts",
            "expected_minutes",
            "p60",
            "p_any",
        ]
        with fixtures_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fixture_fields)
            writer.writeheader()
            for element in range(1, 17):
                writer.writerow(
                    {
                        "element": element,
                        "fpl_code": 1000 + element,
                        "player_name": f"P{element}",
                        "team_name": "Alpha" if element <= 8 else "Beta",
                        "position": "MID" if element >= 6 else "DEF",
                        "gameweek": 4,
                        "fixture": "Alpha v Beta",
                        "kickoff_time": "2026-09-12T14:00:00Z",
                        "xpts": round(2.0 + element / 10.0, 2),
                        "expected_minutes": 80 if element != 15 else 20,
                        "p60": 0.8 if element != 15 else 0.1,
                        "p_any": 0.95 if element != 15 else 0.4,
                    }
                )

        acceptance_path = projections / "dastan_gw4_acceptance.json"
        acceptance_path.write_text(
            json.dumps(
                {
                    "schema": "dastan-smartplay-free-acceptance-v3",
                    "generated_at": "2026-09-07T09:00:00Z",
                    "season": "2026-27",
                    "gameweek": 4,
                    "deadline_time": "2026-09-12T12:30:00Z",
                    "fpl_is_next": True,
                    "fixture_rows": 16,
                    "player_rows": 16,
                    "current_mapping_count": 16,
                    "parity_gate": {"status": "EARLY_SEASON_DIAGNOSTIC", "passed": False},
                }
            ),
            encoding="utf-8",
        )

        picks = []
        picks.append(
            {
                "id": 1,
                "week": 4,
                "name": "P1",
                "pos": "DEF",
                "team": "Alpha",
                "xP": 2.1,
                "xMin": 80,
                "squad": 0,
                "lineup": 0,
                "bench": -1,
                "captain": 0,
                "vicecaptain": 0,
                "transfer_in": 0,
                "transfer_out": 1,
                "buy_price": 0,
                "sell_price": 5.1,
            }
        )
        for element in list(range(2, 16)) + [16]:
            lineup = 1 if element in set(range(2, 12)) | {16} else 0
            bench_slot = -1
            if element in range(12, 16):
                bench_slot = element - 12
            picks.append(
                {
                    "id": element,
                    "week": 4,
                    "name": f"P{element}",
                    "pos": "MID" if element >= 6 else "DEF",
                    "team": "Alpha" if element <= 8 else "Beta",
                    "xP": round(2.0 + element / 10.0, 2),
                    "xMin": 80 if element != 15 else 20,
                    "squad": 1,
                    "lineup": lineup,
                    "bench": bench_slot,
                    "captain": 1 if element == 2 else 0,
                    "vicecaptain": 1 if element == 3 else 0,
                    "transfer_in": 1 if element == 16 else 0,
                    "transfer_out": 0,
                    "buy_price": 6.6 if element == 16 else 0,
                    "sell_price": 0,
                }
            )
        solution = {
            "picks": picks,
            "statistics": {"4": {"itb": 0.3, "ft": 0, "pt": 0, "nt": 1, "xP": 55.0, "chip": None}},
            "score": 57.25,
            "total_xp": 54.5,
            "summary": "Buy P16 / Sell P1",
            "meta": {"highs_status": "Optimal"},
        }
        solution_json = solution_dir / "solution.json"
        solution_json.write_text(json.dumps(solution), encoding="utf-8")
        picks_csv = solution_dir / "picks.csv"
        picks_csv.write_text("id,name\n1,P1\n16,P16\n", encoding="utf-8")
        summary = solution_dir / "summary.md"
        summary.write_text("Buy P16 / Sell P1\n", encoding="utf-8")

        manifest = {
            "schema": ai_context.STRATEGY_SCHEMA,
            "entry_id": 63984,
            "gameweek": 4,
            "horizon": 1,
            "team_state": {"source_sha256": ai_context.sha256_file(snapshot)},
            "inputs": {
                "projection_acceptance_sha256": ai_context.sha256_file(acceptance_path),
                "projection_csv_sha256": ai_context.sha256_file(solver_path),
                "projection_fixtures_sha256": ai_context.sha256_file(fixtures_path),
            },
            "solution": {
                "summary_sha256": ai_context.sha256_file(summary),
                "picks_sha256": ai_context.sha256_file(picks_csv),
                "json_sha256": ai_context.sha256_file(solution_json),
            },
            "upstream": {"dastan": "d" * 40, "smartplay_solver": "s" * 40},
            "scientific_boundary": {"projection_horizon": 1, "solver_min_expected_minutes": 1},
        }
        (state / "strategy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return state, snapshot

    def test_bundle_is_ai_ready_and_whitelists_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp))
            bundle = ai_context.build_bundle(state, snapshot)
            self.assertEqual(bundle["schema"], ai_context.AI_CONTEXT_SCHEMA)
            self.assertEqual(bundle["owner_state"]["free_transfers"], 2)
            self.assertEqual(bundle["owner_state"]["bank_millions"], 0.5)
            self.assertEqual(bundle["model_evidence"]["player_count"], 16)
            self.assertEqual(bundle["current_squad"][0]["current_price_tenths"], 52)
            self.assertEqual(bundle["optimizer_evidence"]["transfers"]["out"][0]["name"], "P1")
            self.assertEqual(bundle["optimizer_evidence"]["transfers"]["in"][0]["name"], "P16")
            self.assertEqual(bundle["optimizer_evidence"]["captain"]["name"], "P2")
            self.assertEqual(bundle["optimizer_evidence"]["vice_captain"]["name"], "P3")
            self.assertEqual(len(bundle["optimizer_evidence"]["starting_xi"]), 11)
            self.assertEqual(len(bundle["optimizer_evidence"]["bench"]), 4)
            encoded = json.dumps(bundle)
            self.assertNotIn("TOP_LEVEL_SECRET_MUST_NOT_LEAK", encoded)
            self.assertNotIn("MUST_NOT_LEAK", encoded)

    def test_fixture_probability_evidence_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp))
            bundle = ai_context.build_bundle(state, snapshot)
            p15 = next(row for row in bundle["model_evidence"]["all_players"] if row["element"] == 15)
            self.assertEqual(p15["expected_minutes"], 20.0)
            self.assertEqual(p15["fixtures"][0]["p60"], 0.1)
            self.assertEqual(p15["fixtures"][0]["p_any"], 0.4)

    def test_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp))
            (state / "solution" / "summary.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "summary hash"):
                ai_context.build_bundle(state, snapshot)

    def test_fixture_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp))
            fixture_path = state / "projections" / "dastan_gw4_fixtures.csv"
            fixture_path.write_text(fixture_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixtures_csv hash"):
                ai_context.build_bundle(state, snapshot)

    def test_non_private_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp), scope="PUBLIC_CANONICAL")
            with self.assertRaisesRegex(ValueError, "PRIVATE_MANAGER"):
                ai_context.build_bundle(state, snapshot)

    def test_written_bundle_is_private_and_checksummed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state, snapshot = self.make_state(Path(temp))
            metadata = ai_context.write_bundle(state, snapshot)
            context = state / metadata["context_file"]
            brief = state / metadata["brief_file"]
            checksum = state / metadata["checksum_file"]
            self.assertEqual(context.stat().st_mode & 0o777, 0o600)
            self.assertEqual(brief.stat().st_mode & 0o777, 0o600)
            self.assertEqual(checksum.stat().st_mode & 0o777, 0o600)
            self.assertEqual(metadata["context_sha256"], ai_context.sha256_file(context))
            self.assertIn(metadata["context_sha256"], checksum.read_text(encoding="utf-8"))
            brief_text = brief.read_text(encoding="utf-8")
            self.assertIn("Attach `ai_decision_context.json`", brief_text)
            self.assertIn("- Sell: P1", brief_text)
            self.assertIn("- Buy: P16", brief_text)

    def test_ai_contract_forbids_numeric_invention(self) -> None:
        contract = ai_context.ai_contract()
        joined = " ".join(contract["hard_rules"])
        self.assertIn("Never invent a numerical edge", joined)
        self.assertIn("GW+1", joined)


if __name__ == "__main__":
    unittest.main()
