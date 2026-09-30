"""Acceptance: manual deadlines, and open assessments from the inbox file, surfacing in the plan output."""
import datetime as dt

from tests._support import CliTest


def in_text(target):
    secs = (target - dt.datetime.now()).total_seconds()
    days = int(secs) // 86400
    return "in %dd" % days if days >= 3 else None


class DeadlineTest(CliTest):
    def test_manual_deadline_surfaces_in_plan_with_countdown(self):
        day = self.today + dt.timedelta(days=9)
        self.assertIn("added  17-695 exam", self.out("deadline", "add", "17-695 exam", "%s 08:30" % day.isoformat()))
        lines = self.section(self.out("show"), "DEADLINES")
        self.assertEqual(len(lines), 1)
        self.assertIn("17-695 exam", lines[0])
        self.assertIn("8:30 AM", lines[0])
        self.assertIn(in_text(dt.datetime.combine(day, dt.time(8, 30))), lines[0])

    def test_rolling_deadline_and_ordering(self):
        self.out("deadline", "add", "No date")
        self.out("deadline", "add", "Later", (self.today + dt.timedelta(days=20)).isoformat())
        self.out("deadline", "add", "Sooner", (self.today + dt.timedelta(days=5)).isoformat())
        lines = self.out("deadline").splitlines()
        self.assertEqual(len(lines), 3)
        for line, name in zip(lines, ("Sooner", "Later", "No date")):
            self.assertIn(name, line)
        self.assertIn("rolling", lines[-1])

    def test_done_moves_to_bottom_and_remove(self):
        self.out("deadline", "add", "A", (self.today + dt.timedelta(days=5)).isoformat())
        self.out("deadline", "add", "B", (self.today + dt.timedelta(days=6)).isoformat())
        self.out("deadline", "done", "1")
        lines = self.out("deadline").splitlines()
        self.assertIn("B", lines[0])
        self.assertIn("✓ A", lines[1])
        self.out("deadline", "rm", "2")
        self.assertEqual(len(self.out("deadline").splitlines()), 1)

    def test_bad_time_fails(self):
        self.assertIn("time not understood", self.cli("deadline", "add", "X", "whenever", ok=False).stderr)

    def test_open_assessment_in_inbox_file_shows_as_deadline(self):
        due = (self.today + dt.timedelta(days=6)).isoformat() + " 12:46"
        recent = self.yesterday.isoformat()
        self.put("inbox.json", {"updated": "", "accounts": [], "errors": {}, "items": [
            {"id": "a1", "key": "k1", "kind": "oa", "status": "open", "company": "Susquehanna", "platform": "HackerRank",
             "due": due, "detail": "60 min", "date": recent, "title": "OA invite"},
            {"id": "a2", "key": "k2", "kind": "oa", "status": "done", "company": "Norvia", "platform": "HackerRank",
             "due": "", "detail": "", "date": recent, "title": "OA"},
            {"id": "a3", "key": "k3", "kind": "oa", "status": "hidden", "company": "Hidden Co", "platform": "",
             "due": due, "detail": "", "date": recent, "title": "OA"},
            {"id": "a4", "key": "k4", "kind": "rejection", "status": "open", "company": "NVS", "platform": "",
             "due": "", "detail": "", "date": recent, "title": "Update"}]})
        lines = self.out("deadline").splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("Susquehanna HackerRank", lines[0])
        self.assertIn("(from mail)", lines[0])
        self.assertIn("✓ Norvia HackerRank", lines[1])
        self.assertIn("submitted %s" % "%s %d" % (self.yesterday.strftime("%b"), self.yesterday.day), lines[1])
        self.out("deadline", "done", "1")
        inbox = self.data("inbox.json")
        self.assertEqual([i["status"] for i in inbox["items"] if i["id"] == "a1"], ["done"])
