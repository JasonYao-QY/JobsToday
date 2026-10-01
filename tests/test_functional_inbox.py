"""Functional: the inbox slice — especially merge_inbox's dedup rules, which the mail scan relies on."""
from textwrap import dedent

from tests._support import SliceTest

PRE = dedent("""
    import json, datetime as dt
    try:
        import inbox as M
        M.merge_inbox
    except (ImportError, AttributeError):
        import today as M
    D = lambda n: (dt.date.today() - dt.timedelta(days=n)).isoformat()
    def it(key, kind, company, date, **kw):
        base = {"id": "id-" + key, "key": key, "account": kw.pop("account", "a@x"), "date": date, "company": company,
                "kind": kind, "platform": "", "title": key, "detail": "", "due": "", "due_text": "", "mins": None,
                "status": "done" if kind == "oa_done" else "open", "link": ""}
        base.update(kw)
        return base
""")


class InboxSlice(SliceTest):
    def test_rescan_keeps_status_and_id(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": [it("k1", "rejection", "NVS", D(2), status="hidden")]}
            found = [dict(it("k1", "rejection", "NVS", D(2)), id="new-id")]
            M.merge_inbox(box, found, ["a@x"], {}, scanned_ok={"a@x"})
            print(json.dumps([(i["id"], i["status"]) for i in box["items"]]))
        """))
        self.assertEqual(out, [["id-k1", "hidden"]])

    def test_seed_items_replaced_only_for_scanned_accounts(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": [it("s1", "applied", "A", D(1), source="seed", account="a@x"),
                             it("s2", "applied", "B", D(1), source="seed", account="b@x")]}
            M.merge_inbox(box, [], ["a@x", "b@x"], {"b@x": "login failed"}, scanned_ok={"a@x"})
            print(json.dumps([sorted(i["key"] for i in box["items"]), box["errors"], box["accounts"]]))
        """))
        self.assertEqual(out, [["s2"], {"b@x": "login failed"}, ["a@x", "b@x"]])

    def test_submission_confirmation_closes_open_assessment(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": []}
            M.merge_inbox(box, [it("inv", "oa", "Norvia", D(5)), it("conf", "oa_done", "norvia", D(1))], ["a@x"], {}, {"a@x"})
            print(json.dumps(sorted((i["key"], i["status"]) for i in box["items"])))
        """))
        self.assertEqual(out, [["conf", "done"], ["inv", "done"]])

    def test_duplicate_open_assessments_collapse_and_keep_due_and_platform(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": []}
            found = [it("invite", "oa", "LTC", D(3)),
                     it("platform", "oa", "LTC", D(4), platform="Criteria", due=D(-4), detail="25 min", mins=25),
                     it("later", "oa", "LTC", D(20))]
            M.merge_inbox(box, found, ["a@x"], {}, {"a@x"})
            print(json.dumps(sorted([i["key"], i["platform"], i["due"] != "", i["mins"]] for i in box["items"])))
        """))
        self.assertEqual(out, [["invite", "Criteria", True, 25], ["later", "", False, None]])

    def test_done_assessment_stays_done_and_old_items_drop(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": [it("oa1", "oa", "IMC", D(5), status="done"), it("old", "rejection", "X", D(120))]}
            M.merge_inbox(box, [it("oa1", "oa", "IMC", D(5))], ["a@x"], {}, {"a@x"})
            print(json.dumps([(i["key"], i["status"]) for i in box["items"]]))
        """))
        self.assertEqual(out, [["oa1", "done"]])

    def test_visible_counts_and_summary(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": [it("a", "applied", "A", D(1)), it("r", "rejection", "R", D(2)),
                             it("h", "rejection", "H", D(2), status="hidden"), it("o", "oa", "O", D(3), due=D(-2)),
                             it("i", "interview", "I", D(4)), it("old", "rejection", "Z", D(45))],
                   "updated": "", "accounts": [], "errors": {}}
            vis = [i["key"] for i in M.inbox_visible(box)]
            print(json.dumps([vis, M.inbox_counts(box), M.inbox_summary(box)]))
        """))
        vis, counts, summary = out
        self.assertEqual(vis, ["i", "o", "r"])
        self.assertEqual((counts["applied"], counts["rejection"], counts["oa_open"], counts["interview"]), (1, 1, 1, 1))
        self.assertEqual(summary, "1 OA open · 1 interview · 1 rejection · 1 applied (30d)")

    def test_assessment_deadlines(self):
        out = self.json_out(PRE + dedent("""
            box = {"items": [it("o", "oa", "Larkmont", D(1), platform="HackerRank", due="2099-01-01 12:00", detail="60 min"),
                             it("d", "oa", "Norvia", D(2), status="done"), it("olddone", "oa", "KPN", D(10), status="done"),
                             it("h", "oa", "Hid", D(1), status="hidden"), it("r", "rejection", "NVS", D(1))]}
            print(json.dumps([(r["name"], r["at"], r["done"], r["source"]) for r in M.assessment_deadlines(box)]))
        """))
        self.assertEqual(out, [["Larkmont HackerRank", "2099-01-01 12:00", False, "inbox"], ["Norvia OA", "", True, "inbox"]])
