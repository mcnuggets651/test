from __future__ import annotations

import unittest

import check_acceptance as acceptance


def payload(gameweek: int, *, gate_passed: bool = False, rows: int = 654) -> dict:
    return {
        "schema": "dastan-smartplay-free-acceptance-v3",
        "gameweek": gameweek,
        "fpl_is_next": True,
        "fixture_rows": rows,
        "player_rows": rows,
        "current_mapping_count": 605,
        "smartplay_reference": {"available": True, "matched": 5, "total": 5},
        "parity_gate": {
            "status": "PASS" if gate_passed else "FAIL",
            "passed": gate_passed,
            "mae": 0.1 if gate_passed else 0.7,
            "max_abs_delta": 0.2 if gate_passed else 1.4,
            "rounded_1dp_matches": 5 if gate_passed else 1,
        },
        "outputs": {"fixtures": "fixtures.csv", "solver": "solver.csv"},
    }


class AcceptancePolicyTests(unittest.TestCase):
    def test_gw4_bad_hosted_parity_is_diagnostic_in_auto_mode(self):
        self.assertEqual(acceptance.validate(payload(4), parity_mode="auto"), [])
        summary = acceptance.parity_summary(payload(4), "auto")
        self.assertFalse(summary["required"])
        self.assertEqual(summary["status"], "EARLY_SEASON_DIAGNOSTIC")

    def test_gw6_bad_hosted_parity_fails_auto_mode(self):
        errors = acceptance.validate(payload(6), parity_mode="auto")
        self.assertTrue(any("parity gate failed" in error for error in errors))

    def test_required_mode_fails_even_in_gw4(self):
        errors = acceptance.validate(payload(4), parity_mode="required")
        self.assertTrue(any("parity gate failed" in error for error in errors))

    def test_diagnostic_mode_never_promotes_bad_core_evidence(self):
        broken = payload(4, rows=0)
        errors = acceptance.validate(broken, parity_mode="diagnostic")
        self.assertIn("no fixture-level projection rows", errors)
        self.assertIn("no player-level projection rows", errors)

    def test_gw6_good_hosted_parity_passes_auto_mode(self):
        self.assertEqual(
            acceptance.validate(payload(6, gate_passed=True), parity_mode="auto"), []
        )
        self.assertTrue(acceptance.parity_is_required(payload(6), "auto"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
