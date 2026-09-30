"""Acceptance: recurring daily tasks, checkboxes and counters, per day."""
from tests._support import CliTest


class DailyTest(CliTest):
    def setUp(self):
        super().setUp()
        self.assertIn("(counter to 20)", self.out("daily", "add", "Apply for 20 simplifyjob positions"))
        self.assertNotIn("counter", self.out("daily", "add", "Finish one leetcode problem"))

    def test_counter_increment_clamps_to_target(self):
        self.assertIn("5/20", self.out("daily", "inc", "1", "5"))
        self.assertIn("6/20", self.out("daily", "inc", "1"))
        self.assertIn("20/20", self.out("daily", "inc", "1", "50"))

    def test_done_and_undo_on_counter_and_checkbox(self):
        self.assertIn("20/20", self.out("daily", "done", "1"))
        self.assertIn("✓", self.out("daily", "done", "2"))
        daily = self.section(self.out("show"), "DAILY")
        self.assertIn("20/20", daily[0])
        self.assertIn("[✓] Finish one leetcode problem", daily[1])
        self.out("daily", "undo", "1")
        self.assertIn(" 0/20", self.out("daily", "list"))

    def test_counts_are_per_day(self):
        self.out("daily", "inc", "1", "7")
        self.assertIn(" 7/20", self.out("daily", "list"))
        self.assertIn(" 0/20", self.out("--date", self.tomorrow.isoformat(), "daily", "list"))

    def test_explicit_count_and_removal(self):
        self.out("daily", "add", "Read pages", "--count", "3")
        self.assertIn("0/3", self.out("daily", "list"))
        self.out("daily", "rm", "3")
        self.assertNotIn("Read pages", self.out("daily", "list"))

    def test_daily_file_layout_the_window_reads(self):
        self.out("daily", "inc", "1", "2")
        d = self.data("daily.json")
        self.assertEqual([i["target"] for i in d["items"]], [20, None])
        self.assertEqual(d["log"][self.today.isoformat()][d["items"][0]["id"]]["count"], 2)
