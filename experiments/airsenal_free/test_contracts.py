from __future__ import annotations

import json
import unittest
from pathlib import Path

import horizon_worker
import operational

ROOT = Path(__file__).resolve().parent


class ContractTests(unittest.TestCase):
    def test_pins_are_exact_and_isolated(self):
        pins = json.loads((ROOT / "pins.json").read_text(encoding="utf-8"))
        self.assertEqual(
            pins["upstream_repository"],
            "alan-turing-institute/AIrsenal",
        )
        self.assertRegex(pins["upstream_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            pins["upstream_sha"],
            "453e4797e85232854004a753deda6bcdc81062b5",
        )
        self.assertEqual(pins["upstream_license"], "MIT")
        self.assertEqual(pins["horizons"], [3, 5])
        self.assertEqual(pins["private_results_branch"], "airsenal-results")
        self.assertNotEqual(pins["private_results_branch"], "dastan-results")
        self.assertEqual(pins["private_entry_id"], 63984)
        self.assertEqual(pins["python_version"], "3.12.14")

    def test_bootstrap_never_floats_upstream_or_uses_global_airsenal_home(self):
        text = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('checkout --quiet --detach "$UPSTREAM_SHA"', text)
        self.assertIn('sync --python "$EXACT_PYTHON" --project "$UPSTREAM" --frozen --no-dev', text)
        self.assertIn('UV_PROJECT_ENVIRONMENT="$VENV"', text)
        self.assertIn(".local/share/airsenal-chat", text)
        self.assertIn("exact CPython $PYTHON_VERSION is unavailable; refusing to loosen runtime pin", text)
        self.assertNotIn("checkout main", text)
        self.assertNotIn("uv python install", text)

    def test_no_fpl_write_commands_in_runtime(self):
        joined = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "operational.py",
                "horizon_worker.py",
                "run.sh",
                "bootstrap.sh",
            )
        )
        self.assertNotIn("airsenal_make_transfers", joined)
        self.assertNotIn("airsenal_set_lineup", joined)

    def test_scenario_support_is_upstream_bounded(self):
        valid = horizon_worker.validate_scenario(
            {
                "max_total_hit": 4,
                "allow_unused_transfers": True,
                "max_opt_transfers": 2,
            },
            4,
            None,
        )
        self.assertEqual(valid["max_total_hit"], 4)
        with self.assertRaises(ValueError):
            horizon_worker.validate_scenario({"keep_player": "Bruno"}, 4, None)

    def test_horizons_are_exactly_three_and_five(self):
        self.assertEqual(operational.HORIZONS, (3, 5))


if __name__ == "__main__":
    unittest.main()
