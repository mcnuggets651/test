from __future__ import annotations

import unittest

import pandas as pd

import live_gw_v2 as adapter


class LiveGWV2Tests(unittest.TestCase):
    def bootstrap(self):
        return {
            "teams": [
                {"id": 1, "name": "Arsenal"},
                {"id": 2, "name": "Chelsea"},
            ],
            "elements": [
                {
                    "id": 10,
                    "code": 1001,
                    "web_name": "Alpha",
                    "second_name": "Alpha",
                    "team": 1,
                    "element_type": 3,
                },
                {
                    "id": 11,
                    "code": 1002,
                    "web_name": "Beta",
                    "second_name": "Beta",
                    "team": 2,
                    "element_type": 4,
                },
            ],
        }

    def test_build_export_lookup_uses_official_fpl_identity(self):
        lookup = adapter.build_export_lookup(self.bootstrap()).set_index("fpl_code")
        self.assertEqual(int(lookup.loc[1001, "element"]), 10)
        self.assertEqual(lookup.loc[1001, "player_name"], "Alpha")
        self.assertEqual(lookup.loc[1001, "team_name"], "Arsenal")
        self.assertEqual(lookup.loc[1001, "position"], "MID")
        self.assertEqual(lookup.loc[1002, "position"], "FWD")

    def test_attach_export_metadata_repairs_dastan_display_columns(self):
        predicted = pd.DataFrame(
            [
                {
                    "fpl_code": 1001,
                    "element": 10,
                    "team_name": "Arsenal",
                    "position": "MID",
                    "gameweek": 4,
                    "fixture": 99,
                    "kickoff_time": "2026-09-12T14:00:00Z",
                    "xpts": 5.25,
                    "expected_minutes": 82.0,
                    "p60": 0.8,
                    "p_any": 0.95,
                }
            ]
        )
        repaired = adapter.attach_export_metadata(
            predicted, adapter.build_export_lookup(self.bootstrap())
        )
        self.assertEqual(repaired.loc[0, "player_name"], "Alpha")
        self.assertEqual(repaired.loc[0, "team_name"], "Arsenal")
        self.assertEqual(repaired.loc[0, "position"], "MID")
        self.assertEqual(float(repaired.loc[0, "xpts"]), 5.25)

    def test_attach_export_metadata_rejects_identity_conflict(self):
        predicted = pd.DataFrame(
            [
                {
                    "fpl_code": 1001,
                    "element": 999,
                    "team_name": "Arsenal",
                    "position": "MID",
                }
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "element identity conflicts"):
            adapter.attach_export_metadata(
                predicted, adapter.build_export_lookup(self.bootstrap())
            )

    def test_predeclared_parity_gate_passes_close_reproduction(self):
        comparison = {
            "available": True,
            "matched": 5,
            "total": 5,
            "mae": 0.08,
            "rows": [
                {"smartplay_xpts": 4.6, "xpts": 4.62, "delta": 0.02},
                {"smartplay_xpts": 4.7, "xpts": 4.72, "delta": 0.02},
                {"smartplay_xpts": 3.0, "xpts": 3.04, "delta": 0.04},
                {"smartplay_xpts": 4.1, "xpts": 4.16, "delta": 0.06},
                {"smartplay_xpts": 2.2, "xpts": 2.44, "delta": 0.24},
            ],
        }
        gate = adapter.evaluate_reference(comparison)
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["status"], "PASS")
        self.assertGreaterEqual(gate["rounded_1dp_matches"], 4)

    def test_predeclared_parity_gate_fails_material_drift(self):
        comparison = {
            "available": True,
            "matched": 5,
            "total": 5,
            "mae": 0.7,
            "rows": [
                {"smartplay_xpts": 4.6, "xpts": 5.3, "delta": 0.7},
                {"smartplay_xpts": 4.7, "xpts": 5.4, "delta": 0.7},
                {"smartplay_xpts": 3.0, "xpts": 3.7, "delta": 0.7},
                {"smartplay_xpts": 4.1, "xpts": 4.8, "delta": 0.7},
                {"smartplay_xpts": 2.2, "xpts": 2.9, "delta": 0.7},
            ],
        }
        gate = adapter.evaluate_reference(comparison)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["status"], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
