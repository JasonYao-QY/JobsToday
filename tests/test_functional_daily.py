"""Functional: the daily slice through its public functions."""
from tests._support import SliceTest


class DailySlice(SliceTest):
    def test_counter_checkbox_and_log_pruning(self):
        out = self.json_out("""
            import json, datetime as dt, daily
            d = daily.load_daily()
            d["items"] += [{"id": "c", "title": "Apply for 20 jobs", "target": daily.guess_target("Apply for 20 jobs")},
                           {"id": "b", "title": "Finish one leetcode problem", "target": daily.guess_target("Finish one leetcode problem")}]
            today = dt.date.today().isoformat()
            old = (dt.date.today() - dt.timedelta(days=120)).isoformat()
            c, b = d["items"]
            daily.daily_set(d, c, today, count=25)          # clamps to the target
            daily.daily_set(d, b, today, done=True)
            daily.daily_set(d, c, old, count=3)
            daily.save_daily(d)
            d = daily.load_daily()
            print(json.dumps([daily.daily_state(d, c, today), daily.daily_state(d, b, today), old in d["log"],
                              [i["target"] for i in d["items"]], daily.guess_target("Read 1 chapter")]))
        """)
        self.assertEqual(out, [[True, 20], [True, 0], False, [20, None], None])
