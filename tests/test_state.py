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
