"""Functional: report_applied() touches the tracker and the daily counter, once per application, and a
later confirmation email joins the same record instead of adding a second one."""
from textwrap import dedent

from tests._support import CliTest, SliceTest


class ReportSlice(SliceTest):
    def test_new_duplicate_and_mail_reconciliation(self):
        out = self.json_out(dedent("""
            import json, daily, tracker, report
            from store import APP_DIR
            from timeutil import today_str
            d = daily.load_daily()
            d["items"] = [{"id": "d-leet", "title": "Finish one leetcode problem", "target": None},
                          {"id": "d-apply", "title": "Apply for 20 simplifyjob positions", "target": 20}]
            daily.save_daily(d)
            r1 = report.report_applied("Paylinq", "Software Engineer, Intern (Summer 2027)")
            r2 = report.report_applied("stripe", "Software Engineer Intern")          # same application again
            r3 = report.report_applied("Paylinq", "Data Science Intern")               # a second role
            tr = tracker.load(APP_DIR)
            tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "Paylinq",
                                       "role": "Software Engineer, Intern (Summer 2027)", "title": "Thanks for applying",
                                       "account": "me@gmail.com"}])
            rows = [tracker.derive(a) for a in tr["apps"]]
            d = daily.load_daily()
            print(json.dumps([r1["new"], r2["new"], r3["new"], r1["daily"], r2["daily"], len(rows),
                              sorted([e["source"] for e in r["events"]] for r in rows),
                              daily.daily_state(d, d["items"][1], today_str())[1]]))
        """))
        new1, new2, new3, daily1, daily2, n_apps, sources, count = out
        self.assertEqual((new1, new2, new3), (True, False, True))
        self.assertEqual(daily1, {"title": "Apply for 20 simplifyjob positions", "count": 1, "target": 20})
        self.assertIsNone(daily2)
        self.assertEqual(n_apps, 2)
        self.assertEqual(sources, [["reported"], ["reported", "reported", "mail"]])
        self.assertEqual(count, 2)

    def test_explicit_counter_marker_wins(self):
        out = self.json_out(dedent("""
            import json, daily, report
            from timeutil import today_str
            d = daily.load_daily()
            d["items"] = [{"id": "a", "title": "Apply to 5 hackathons", "target": 5},
                          {"id": "b", "title": "Job goal", "target": 10, "counts": "applications"}]
            daily.save_daily(d)
            print(json.dumps(report.report_applied("Acme", "SWE Intern")["daily"]))
        """))
        self.assertEqual(out, {"title": "Job goal", "count": 1, "target": 10})


class ReportCli(CliTest):
    def test_report_command(self):
        self.out("daily", "add", "Apply for 20 simplifyjob positions")
        self.assertIn("recorded: Paylinq · SWE Intern  (Apply for 20 simplifyjob positions 1/20)", self.out("report", "Paylinq", "SWE Intern"))
        self.assertIn("already tracked: Paylinq · SWE Intern", self.out("report", "Paylinq", "SWE Intern"))
        self.assertIn(" 1/20", self.out("daily", "list"))
        self.assertIn("1 applications", self.out("apps"))
        self.assertIn("which company?", self.cli("report", "", ok=False).stderr)
