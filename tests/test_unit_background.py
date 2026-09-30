"""Unit: BackgroundTask (one job at a time, progress and result pushed, errors reported)."""
import threading
import unittest

import tests._support  # noqa: F401
from background import BackgroundTask


class Background(unittest.TestCase):
    def setUp(self):
        self.events, self.errors = [], []
        self.done = threading.Event()

        def push(event, payload):
            self.events.append((event, payload))
            if payload.get("done"):
                self.done.set()
        self.task = BackgroundTask("scan", push, "onScan", on_error=lambda e, tb: self.errors.append((str(e), "Traceback" in tb)))

    def test_progress_then_result_and_one_at_a_time(self):
        gate = threading.Event()

        def work(progress, n):
            progress("step 1")
            gate.wait(5)
            return {"found": n}
        self.assertTrue(self.task.start(work, 7))
        self.assertTrue(self.task.running)
        self.assertFalse(self.task.start(work, 8))        # already running
        gate.set()
        self.assertTrue(self.done.wait(5))
        self.assertEqual(self.events, [("onScan", {"progress": "step 1"}), ("onScan", {"found": 7, "done": True})])
        self.assertFalse(self.task.running)

    def test_failure_reports_and_frees_the_slot(self):
        def work(progress):
            raise RuntimeError("imap down")
        self.assertTrue(self.task.start(work))
        self.assertTrue(self.done.wait(5))
        self.assertEqual(self.events[-1], ("onScan", {"errors": {"scan": "imap down"}, "done": True}))
        self.assertEqual(self.errors, [("imap down", True)])
        self.assertFalse(self.task.running)
