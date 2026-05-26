import sys
import time
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from artifacts import InProcessArtifactStore  # noqa: E402


class InProcessArtifactStoreTests(unittest.TestCase):
    def test_register_and_metadata(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        path = Path(__file__)
        store.register(path, "owner-1", "test")

        meta = store.metadata(path.name)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["owner_id"], "owner-1")
        self.assertEqual(meta["artifact_kind"], "test")

    def test_metadata_returns_none_for_missing(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        self.assertIsNone(store.metadata("missing.wav"))

    def test_touch_owner_updates_expiry(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        path = Path(__file__)
        store.register(path, "owner-1", "test")
        meta = store.metadata(path.name)
        old_expiry = meta["expires_at"]

        time.sleep(0.01)

        changed = store.touch_owner("owner-1")
        self.assertTrue(changed)
        new_meta = store.metadata(path.name)
        self.assertGreater(new_meta["expires_at"], old_expiry)

    def test_touch_owner_returns_false_when_no_match(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        changed = store.touch_owner("nonexistent")
        self.assertFalse(changed)

    def test_remove_expired(self):
        store = InProcessArtifactStore(ttl_seconds=0)
        path = Path(__file__)
        store.register(path, "owner-1", "test")

        expired = store.remove_expired(time.time() + 1)
        self.assertEqual(len(expired), 1)
        self.assertIsNone(store.metadata(path.name))

    def test_remove_by_owner(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        path = Path(__file__)
        store.register(path, "owner-1", "test")

        removed = store.remove_by_owner("owner-1")
        self.assertEqual(len(removed), 1)
        self.assertIsNone(store.metadata(path.name))

    def test_remove_by_owner_returns_empty_when_no_match(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        removed = store.remove_by_owner("nonexistent")
        self.assertEqual(removed, [])

    def test_contains(self):
        store = InProcessArtifactStore(ttl_seconds=60)
        path = Path(__file__)
        store.register(path, "owner-1", "test")

        self.assertIn(path.name, store)
        self.assertNotIn("missing.wav", store)


if __name__ == "__main__":
    unittest.main()
