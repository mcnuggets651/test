from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import doctor

ROOT = Path(__file__).resolve().parent


class ResilienceContracts(unittest.TestCase):
    def test_run_pipeline_has_pre_and_post_doctor_and_direct_mode(self):
        text = (ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn("--phase pre", text)
        self.assertIn("--phase post", text)
        self.assertIn("--doctor-only", text)
        self.assertIn('"$SCRIPT_DIR/bootstrap.sh"', text)
        self.assertIn('"$SCRIPT_DIR/operational.py"', text)

    def test_bootstrap_has_verified_exact_cache_short_circuit(self):
        text = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("cache_is_exact", text)
        self.assertIn("airsenal-chat-runtime-v2", text)
        self.assertIn("installed_distributions_sha256", text)
        self.assertIn("pins_sha256", text)
        self.assertIn("AIrsenal cached runtime verified", text)
        self.assertIn('checkout --quiet --detach "$UPSTREAM_SHA"', text)
        self.assertNotIn("checkout main", text)

    def test_doctor_github_slug(self):
        self.assertEqual(doctor.github_slug("https://github.com/mcnuggets651/fpl.git"), "mcnuggets651/fpl")
        self.assertEqual(doctor.github_slug("git@github.com:mcnuggets651/fpl.git"), "mcnuggets651/fpl")
        self.assertIsNone(doctor.github_slug("https://example.com/x/y"))

    def test_runtime_cache_missing_is_safe_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            result = doctor.runtime_cache_summary(Path(td), {"upstream_sha": "x"})
        self.assertFalse(result["present"])
        self.assertFalse(result["candidate_valid"])

    def test_health_namespace_is_in_pointer_commit_contract(self):
        text = (ROOT / "publish_chat_bridge.py").read_text(encoding="utf-8")
        self.assertIn('Path("airsenal") / "health"', text)
        self.assertIn('HEALTH_SCHEMA = "airsenal-chat-health-v1"', text)
        self.assertIn('git(worktree, "add", str(pointer_rel), str(health_rel))', text)
        self.assertNotIn("RUNNER_NAME", text)


if __name__ == "__main__":
    unittest.main()
