from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import operational


class OperationalAiCleanupTests(unittest.TestCase):
    def test_generate_ai_sidecar_removes_stale_files_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            snapshot = output / "snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            for name in operational.AI_SIDECAR_FILES:
                (output / name).write_text("STALE", encoding="utf-8")

            def fake_write_bundle(state_dir: Path, _snapshot: Path):
                self.assertFalse(any((state_dir / name).exists() for name in operational.AI_SIDECAR_FILES))
                (state_dir / "ai_decision_context.json").write_text("fresh", encoding="utf-8")
                return {"status": "ready", "context_file": "ai_decision_context.json"}

            with mock.patch("ai_context.write_bundle", side_effect=fake_write_bundle):
                result = operational.generate_ai_sidecar(snapshot, output)

            self.assertEqual(result["status"], "ready")
            self.assertEqual((output / "ai_decision_context.json").read_text(encoding="utf-8"), "fresh")

    def test_generate_ai_sidecar_removes_partial_and_stale_files_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            snapshot = output / "snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            for name in operational.AI_SIDECAR_FILES:
                (output / name).write_text("STALE", encoding="utf-8")

            def failing_write_bundle(state_dir: Path, _snapshot: Path):
                (state_dir / "ai_decision_context.json").write_text("PARTIAL", encoding="utf-8")
                raise ValueError("synthetic sidecar failure")

            with mock.patch("ai_context.write_bundle", side_effect=failing_write_bundle):
                with self.assertRaisesRegex(ValueError, "synthetic sidecar failure"):
                    operational.generate_ai_sidecar(snapshot, output)

            self.assertFalse(any((output / name).exists() for name in operational.AI_SIDECAR_FILES))


if __name__ == "__main__":
    unittest.main()
