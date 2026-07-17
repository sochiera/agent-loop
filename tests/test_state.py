from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge.state import State


class StateCheckpointTest(unittest.TestCase):
    def test_review_checkpoint_survives_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATE.json"
            state = State(iteration=7, phase="review", current_task_title="Walka",
                          fix_attempt=2, tests_green=True,
                          review_notes=["Dodaj test regresyjny"])

            state.save(str(path))
            restored = State.load(str(path))

        self.assertEqual(restored.phase, "review")
        self.assertEqual(restored.current_task_title, "Walka")
        self.assertEqual(restored.fix_attempt, 2)
        self.assertTrue(restored.tests_green)
        self.assertEqual(restored.review_notes, ["Dodaj test regresyjny"])


if __name__ == "__main__":
    unittest.main()

class VerifyStateTest(unittest.TestCase):
    def test_verify_profile_and_checkpoint_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATE.json"
            state = State(verify_targets=["ci", "hardware"],
                          smoke_cmd="bash scripts/smoke.sh",
                          flash_cmd="bash scripts/flash.sh",
                          target_cmd="bash scripts/hil.sh",
                          probe_cmd="bash scripts/probe.sh",
                          ci_status_cmd="bash scripts/ci-status.sh {sha}",
                          ci_logs_cmd="bash scripts/ci-logs.sh {sha}",
                          verify_test_globs=["tests/hil/**"],
                          verify_cycle=2, verify_stall=1, verify_sha="abc123",
                          verify_problems=[{"id": "P-001", "status": "persisting",
                                            "class": "code_bug", "title": "t"}])

            state.save(str(path))
            restored = State.load(str(path))

        self.assertEqual(restored.verify_targets, ["ci", "hardware"])
        self.assertEqual(restored.ci_status_cmd, "bash scripts/ci-status.sh {sha}")
        self.assertEqual(restored.verify_test_globs, ["tests/hil/**"])
        self.assertEqual(restored.verify_cycle, 2)
        self.assertEqual(restored.verify_stall, 1)
        self.assertEqual(restored.verify_sha, "abc123")
        self.assertEqual(restored.verify_problems[0]["id"], "P-001")

    def test_pre_verification_state_files_migrate_to_disabled_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATE.json"
            State(bootstrapped=True, test_cmd="pytest").save(str(path))
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in list(data):
                if key.startswith("verify") or key in ("smoke_cmd", "flash_cmd",
                                                       "target_cmd", "probe_cmd",
                                                       "ci_status_cmd", "ci_logs_cmd"):
                    del data[key]
            path.write_text(json.dumps(data), encoding="utf-8")

            restored = State.load(str(path))

        self.assertEqual(restored.verify_targets, [])
        self.assertEqual(restored.verify_cycle, 0)
        self.assertEqual(restored.verify_problems, [])
