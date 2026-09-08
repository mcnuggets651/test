from __future__ import annotations

import json
import unittest
from pathlib import Path

import horizon_worker

ROOT = Path(__file__).resolve().parent


class InteractiveNoHitProfileTests(unittest.TestCase):
    def test_profile_uses_supported_airsenal_knobs(self):
        raw = json.loads((ROOT / "interactive_nohit_h3h5.json").read_text(encoding="utf-8"))
        scenario = horizon_worker.validate_scenario(raw, 4, None)
        self.assertEqual(scenario["max_total_hit"], 0)
        self.assertFalse(scenario["allow_unused_transfers"])
        self.assertEqual(scenario["max_opt_transfers"], 2)
        self.assertEqual(scenario["num_iterations"], 100)
        self.assertEqual(scenario["num_thread"], 8)
        self.assertEqual(scenario["chip_gameweeks"], {})

    def test_reference_defaults_remain_unchanged(self):
        scenario = horizon_worker.validate_scenario({}, 4, None)
        self.assertEqual(scenario["max_total_hit"], 8)
        self.assertFalse(scenario["allow_unused_transfers"])
        self.assertEqual(scenario["max_opt_transfers"], 2)
        self.assertEqual(scenario["num_iterations"], 100)
        self.assertEqual(scenario["num_thread"], 4)


if __name__ == "__main__":
    unittest.main()
