"""Unit: store (JSON read/write, ids)."""
import os
import shutil
import tempfile
import unittest

import tests._support  # noqa: F401
import store


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_round_trip_and_atomic_write(self):
        p = os.path.join(self.dir, "sub", "x.json")
        store.save_json(p, {"a": "é", "n": [1, 2]})
        self.assertEqual(store.load_json(p, None), {"a": "é", "n": [1, 2]})
        self.assertEqual(os.listdir(os.path.dirname(p)), ["x.json"])   # no temp file left behind

    def test_missing_or_broken_file_gives_default(self):
        p = os.path.join(self.dir, "bad.json")
        self.assertEqual(store.load_json(p, {"d": 1}), {"d": 1})
        with open(p, "w") as f:
            f.write("{not json")
        self.assertEqual(store.load_json(p, []), [])

    def test_ids(self):
        ids = {store.new_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        self.assertTrue(all(len(i) == 8 and int(i, 16) >= 0 for i in ids))
