from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import publish_chat_bridge


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True).stdout.strip()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_bundle(root: Path) -> str:
    (root / "h3").mkdir(parents=True)
    (root / "h5").mkdir(parents=True)
    write_json(root / "owner_state.json", {"schema":"airsenal-chat-owner-state-v1","entry_id":63984})
    write_json(root / "official_fpl_provenance.json", {"schema":"airsenal-chat-official-fpl-v1","target_gameweek":4})
    write_json(root / "h3/result.json", {"schema":"airsenal-chat-horizon-result-v1","horizon":3})
    write_json(root / "h5/result.json", {"schema":"airsenal-chat-horizon-result-v1","horizon":5})
    files={rel:hashlib.sha256((root/rel).read_bytes()).hexdigest() for rel in ("owner_state.json","official_fpl_provenance.json","h3/result.json","h5/result.json")}
    context={
      "schema":"airsenal-chat-decision-context-v1",
      "run_timestamp":"2026-09-07T12:00:00+00:00",
      "entry_id":63984,"target_gameweek":4,
      "airsenal_result":{"h3":{},"h5":{}},
      "integrity":{"files":files},
      "privacy":{"classification":"PRIVATE_MANAGER_LOCAL_ONLY","safe_for_public_artifact_upload":False},
      "provenance":{"experiment_code_sha":"a"*40,"airsenal_upstream_sha":"b"*40},
      "private_authority":{"run_id":"r"},
    }
    write_json(root/"decision_context.json", context)
    sha=hashlib.sha256((root/"decision_context.json").read_bytes()).hexdigest()
    (root/"decision_context.sha256").write_text(f"{sha}  decision_context.json\n",encoding="utf-8")
    write_json(root/"run_manifest.json", {"schema":"airsenal-chat-local-run-v1","status":"ready"})
    return sha


class HealthTest(unittest.TestCase):
    def test_health_is_written_in_pointer_commit_and_contains_no_machine_identity(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); remote=base/"remote.git"; repo=base/"work"; bundle=base/"bundle"
            subprocess.run(["git","init","--bare",str(remote)],check=True,capture_output=True)
            subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
            git(repo,"config","user.name","Test"); git(repo,"config","user.email","test@example.invalid")
            (repo/"seed.txt").write_text("seed\n"); git(repo,"add","seed.txt"); git(repo,"commit","-m","seed"); git(repo,"branch","-M","main")
            git(repo,"remote","add","origin",str(remote)); git(repo,"push","-u","origin","main"); git(repo,"branch","airsenal-results"); git(repo,"push","origin","airsenal-results")
            make_bundle(bundle)
            result=publish_chat_bridge.publish(run_dir=bundle,private_repo=repo,require_github_origin=False)
            health_raw=git(repo,"show",f"{result['pointer_commit_sha']}:{result['health_path']}")
            health=json.loads(health_raw)
            self.assertEqual(health["schema"],"airsenal-chat-health-v1")
            self.assertEqual(health["run_commit_sha"],result["run_commit_sha"])
            self.assertEqual(health["horizons"],[3,5])
            self.assertNotIn("runner_name",json.dumps(health).lower())
            self.assertNotIn("christos",json.dumps(health).lower())
            parent=git(repo,"rev-parse",f"{result['pointer_commit_sha']}^")
            self.assertEqual(parent,result["run_commit_sha"])


if __name__=="__main__":
    unittest.main()
