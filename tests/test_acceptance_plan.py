"""Acceptance: day plans through the CLI (add, done, move, remove, edit, planning ahead, carry-over)."""
from tests._support import CliTest, short


class PlanTest(CliTest):
    def test_add_prints_and_shows_with_time_length_note_urgent(self):
        self.assertIn("added  6:10 PM Norvia OA", self.out("add", "6:10pm", "Norvia OA", "--min", "75", "--note", "one sitting", "--urgent"))
        sched = self.section(self.out("show"), "SCHEDULE")
        self.assertEqual(sched[0], "   1.  6:10 PM   Norvia OA · 75m ⚠")
        self.assertEqual(sched[1].strip(), "└ one sitting")

    def test_timed_items_sort_before_untimed_in_time_order(self):
        self.out("add", "Laundry")
        self.out("add", "6pm", "Dinner")
        self.out("add", "9am", "Gym")
        titles = [l.split()[-1] for l in self.section(self.out("show"), "SCHEDULE")]
        self.assertEqual(titles, ["Gym", "Dinner", "Laundry"])

    def test_done_undo_and_progress(self):
        self.out("add", "9am", "Gym")
        self.out("add", "Laundry")
        self.assertIn("done  Gym", self.out("done", "1"))
        show = self.out("show")
        self.assertIn("1/2 done", show)
        self.assertIn(" 9:00 AM ✓ Gym", show)
        self.out("undo", "1")
        self.assertIn("0/2 done", self.out("show"))

    def test_move_changes_time_and_resorts(self):
        self.out("add", "9am", "Gym")
        self.out("add", "10am", "Lecture")
        self.assertIn("move  Gym", self.out("move", "1", "7pm"))
        sched = self.section(self.out("show"), "SCHEDULE")
        self.assertTrue(sched[0].endswith("Lecture") and sched[1].endswith("Gym") and "7:00 PM" in sched[1], sched)

    def test_remove_edit_note(self):
        self.out("add", "A")
        self.out("add", "B")
        self.out("rm", "1")
        self.out("edit", "1", "B renamed")
        self.out("note", "1", "bring ID")
        sched = self.section(self.out("show"), "SCHEDULE")
        self.assertEqual(len(sched), 2)
        self.assertTrue(sched[0].endswith("B renamed"))
        self.assertEqual(sched[1].strip(), "└ bring ID")

    def test_bad_number_and_bad_time_fail(self):
        self.out("add", "A")
        self.assertIn("no item #9", self.cli("done", "9", ok=False).stderr)
        self.assertIn("time not understood", self.cli("move", "1", "lunchish", ok=False).stderr)

    def test_plan_for_tomorrow_and_another_date(self):
        self.out("tomorrow", "add", "8am", "StoneRidge OA")
        self.assertIn("nothing planned", self.out("show"))
        show = self.out("--date", self.tomorrow.isoformat(), "show")
        self.assertIn("(%s)" % self.tomorrow.isoformat(), show)
        self.assertIn("StoneRidge OA", show)

    def test_carry_over_unfinished_from_previous_plan_once(self):
        y = self.yesterday.isoformat()
        self.out("--date", y, "add", "9am", "Finished thing")
        self.out("--date", y, "add", "Unfinished thing")
        self.out("--date", y, "done", "1")
        self.assertIn("carried 1 item from %s" % short(self.yesterday), self.out("carry"))
        self.assertIn("Unfinished thing", self.out("show"))
        self.assertIn("carried 0 items", self.out("carry"))

    def test_no_arguments_prints_the_plan_when_not_a_terminal(self):
        out = self.out()
        self.assertTrue(out.startswith("TODAY  "), out[:80])
        self.assertIn("SCHEDULE", out)

    def test_plan_file_layout_the_window_reads(self):
        self.out("add", "9:30am", "Lecture", "--min", "80")
        plan = self.data("plans/%s.json" % self.today.isoformat())
        self.assertEqual(plan["date"], self.today.isoformat())
        item = plan["items"][0]
        self.assertEqual(set(item), {"id", "title", "time", "note", "mins", "urgent", "done", "created"})
        self.assertEqual((item["time"], item["mins"], item["done"]), ("09:30", 80, False))

    def test_where_reports_the_data_folder(self):
        self.assertEqual(self.out("where").strip(), self.home)
