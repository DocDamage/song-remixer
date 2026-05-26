import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from jobs import InProcessJobStore  # noqa: E402


class InProcessJobStoreTests(unittest.TestCase):
    def test_completed_jobs_enter_history_newest_first(self):
        store = InProcessJobStore(ttl_seconds=60)
        store.create("one", "auto-mix", {"beat": "a.wav"})
        store.create("two", "split-stems", {"track": "b.wav"})

        store.complete("one", {"status_line": "first done"})
        store.complete("two", {"status_line": "second done"})

        history = store.list_history()
        self.assertEqual([entry["job_id"] for entry in history], ["two", "one"])
        self.assertEqual(history[0]["status"], "completed")

    def test_failed_job_keeps_error_message(self):
        store = InProcessJobStore(ttl_seconds=60)
        store.create("job", "auto-mix", {})

        store.fail("job", "render failed")

        snapshot = store.snapshot("job")
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "render failed")


if __name__ == "__main__":
    unittest.main()
