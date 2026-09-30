"""Functional: the deadlines slice through its public functions."""
from tests._support import SliceTest


class DeadlinesSlice(SliceTest):
    def test_merge_sort_and_labels(self):
        out = self.json_out("""
            import json, datetime as dt, deadlines
            d = deadlines.load_deadlines()
            d["items"] += [{"id": "r", "name": "Rolling", "at": "", "note": "", "done": False},
                           {"id": "l", "name": "Later", "at": "2099-02-01", "note": "", "done": False},
                           {"id": "s", "name": "Sooner", "at": "2099-01-01 09:00", "note": "", "done": False},
                           {"id": "x", "name": "Finished", "at": "2099-01-01", "note": "", "done": True}]
            deadlines.save_deadlines(d)
            y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
            box = {"items": [{"id": "o", "kind": "oa", "status": "done", "company": "Norvia", "platform": "HackerRank",
                              "due": "", "detail": "", "date": y}]}
            rows = deadlines.all_deadlines(deadlines.load_deadlines(), box)
            print(json.dumps([[r["name"], r["sev"], deadlines.deadline_when(r)] for r in rows]))
        """)
        names = [r[0] for r in out]
        self.assertEqual(names[:3], ["Sooner", "Later", "Rolling"])
        self.assertEqual(sorted(names[3:]), ["Finished", "Norvia HackerRank"])
        self.assertEqual([r[1] for r in out[:3]], ["ok", "ok", "none"])
        self.assertEqual(out[2][2], "rolling")
        self.assertTrue([r for r in out if r[0] == "Norvia HackerRank"][0][2].startswith("submitted "))
