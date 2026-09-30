"""Functional: the plans slice through its public functions."""
from tests._support import SliceTest


class PlansSlice(SliceTest):
    def test_round_trip_and_sorting(self):
        out = self.json_out("""
            import json, plans
            p = plans.load_plan("2026-10-10")
            plans.add_item(p, "Laundry")
            plans.add_item(p, "17-695 exam", "08:30", mins=180)
            plans.add_item(p, "Lunch", "12:00")
            plans.save_plan(p)
            q = plans.load_plan("2026-10-10")
            print(json.dumps([i["title"] for i in plans.sorted_items(q["items"])] + [q["date"]]))
        """)
        self.assertEqual(out, ["17-695 exam", "Lunch", "Laundry", "2026-10-10"])

    def test_carry_from_most_recent_earlier_plan_dedupes_by_title(self):
        out = self.json_out("""
            import json, plans
            for d, titles in (("2026-09-27", ["Old"]), ("2026-09-29", ["Keep A", "Keep B", "Done C"])):
                p = plans.load_plan(d)
                for t in titles:
                    plans.add_item(p, t)
                plans.save_plan(p)
            p = plans.load_plan("2026-09-29"); p["items"][2]["done"] = True; plans.save_plan(p)
            today = plans.load_plan("2026-09-30"); plans.add_item(today, "keep a")
            n, src = plans.carry_over(today)
            print(json.dumps([n, src, [i["title"] for i in today["items"]], plans.plan_dates()]))
        """)
        self.assertEqual(out, [1, "2026-09-29", ["keep a", "Keep B"], ["2026-09-27", "2026-09-29"]])
