"""Functional: the Jobs tab's Api methods end to end against a throwaway data folder.

Api.jobs_* -> jobs/batch.py -> tracker, report and the daily counter. The feed is a file written into the
cache and the network is closed off: any attempt to open a URL fails the test.
"""
import re
from textwrap import dedent

from tests._support import SliceTest

PRELUDE = dedent("""
    import json, os, threading, time, urllib.request, urllib.error

    def no_network(*a, **k):
        raise urllib.error.URLError("the tests have no network")
    urllib.request.urlopen = no_network

    import daily, tracker
    from store import APP_DIR
    from timeutil import today_str
    from jobs import batch
    import today_gui as G

    AFTER = batch._cutoff_epoch(batch.DEFAULT_CUTOFF) + 2 * 86400

    def raw(company, title="Software Engineer Intern", posted=AFTER):
        return {"id": company, "source": "Simplify", "category": "Software", "company_name": company,
                "title": title, "active": True, "is_visible": True, "terms": ["Summer 2027"],
                "date_posted": posted, "date_updated": posted, "url": "https://example.com/" + company,
                "locations": ["NYC"], "sponsorship": "Other", "degrees": ["Bachelor's", "Master's"]}

    def feed(rows):
        os.makedirs(batch.CACHE_DIR, exist_ok=True)
        with open(os.path.join(batch.CACHE_DIR, "simplify.json"), "w") as f:
            json.dump(rows, f)

    pushed, done = [], threading.Event()
    class Win:
        def evaluate_js(self, js):
            pushed.append(js)
            if '"done": true' in js:
                done.set()
    api = G.Api(); api._window = Win()

    def wait():
        assert done.wait(30), pushed
        done.clear()
        for _ in range(200):                 # the slot is freed just before the result is pushed
            if not api._jobs.running:
                return
            time.sleep(0.01)

    def last():
        js = pushed[-1]
        assert js.startswith("window.onJobs && window.onJobs("), js
        return json.loads(js[len("window.onJobs && window.onJobs("):-1])

    d = daily.load_daily()
    d["items"] = [{"id": "d-apply", "title": "Apply for 20 simplifyjob positions", "target": 20}]
    daily.save_daily(d)
""")


class WindowJobs(SliceTest):

    def run_script(self, body):
        return self.json_out(PRELUDE + dedent(body))

    def test_state_before_anything_is_renderable_and_creates_nothing(self):
        """1."""
        r = self.run_script("""
            st = api.jobs_state()
            whole = api.state()
            print(json.dumps({"st": st, "badge": whole["jobs_unreported"],
                              "data_dir": os.path.exists(batch.DATA_DIR)}))
        """)
        st = r["st"]
        self.assertNotIn("error", st)
        self.assertEqual((st["ready"], st["rows"], st["unreported"], st["batch_id"]), (False, [], [], None))
        self.assertEqual((st["verifier"], st["running"], st["cutoff_date"]), ("none", False, "2026-09-24"))
        self.assertEqual(r["badge"], 0)
        self.assertFalse(r["data_dir"])

    def test_version_changes_when_a_batch_is_created(self):
        """2. Without the jobs database in version(), the page would stop noticing."""
        r = self.run_script("""
            feed([raw("Alder"), raw("Birch")])
            v0 = api.version()["v"]
            assert api.jobs_prepare(20) == {"started": True}
            wait()
            v1 = api.version()["v"]
            same = api.version()["v"]
            api.jobs_state(); api.state()
            quiet = api.version()["v"]
            time.sleep(0.02)
            api.jobs_skip([1])
            v2 = api.version()["v"]
            print(json.dumps({"changed": v0 != v1, "stable": v1 == same == quiet, "again": v1 != v2,
                              "done": last(), "jobs_running": api.version()["jobs_running"]}))
        """)
        self.assertTrue(r["changed"])
        self.assertTrue(r["stable"])                      # reading the tab does not look like a change
        self.assertTrue(r["again"])
        self.assertFalse(r["jobs_running"])
        d = r["done"]
        self.assertEqual((d["kind"], d["created"], d["rows"], d["found"], d["requested"], d["short"]),
                         ("prepare", True, 2, 2, 20, True))
        self.assertIn("URLError", d["feed_error"])        # no network here: the cached feed was used

    def test_prepare_twice_runs_once(self):
        """3. A second press while the first is running is refused; one batch is made, not two."""
        r = self.run_script("""
            feed([raw("Alder"), raw("Birch")])
            gate, real = threading.Event(), batch.prepare
            def slow(conn, n=20, **k):
                gate.wait(10)
                return real(conn, n=n, **k)
            batch.prepare = slow
            first = api.jobs_prepare(20)
            second = api.jobs_prepare(20)
            verify_while_busy = api.jobs_verify(1)
            running = api.jobs_state()["running"]
            writes = [api.jobs_report_through(1), api.jobs_skip([1]), api.jobs_close_batch()]
            reads = [api.jobs_why("Alder"), api.jobs_state()]
            gate.set(); wait()
            with __import__("contextlib").closing(batch.connect()) as conn:
                n_batches = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
            st = api.jobs_state()
            print(json.dumps({"first": first, "second": second, "verify": verify_while_busy, "running": running,
                              "writes": writes, "reads_ok": ["error" not in r for r in reads],
                              "batches": n_batches, "rows": len(st["rows"]), "after": st["running"],
                              "progress": sum('"progress"' in p for p in pushed),
                              "badge": api.state()["jobs_unreported"]}))
        """)
        self.assertEqual(r["first"], {"started": True})
        self.assertEqual(r["second"], {"started": False, "running": True})
        self.assertEqual(r["verify"], {"started": False, "running": True})
        for w in r["writes"]:                             # nothing may change the batch under a prepare
            self.assertIn("still working", w["error"])
        self.assertEqual(r["reads_ok"], [True, True])     # reading is fine
        self.assertTrue(r["running"])
        self.assertFalse(r["after"])
        self.assertEqual((r["batches"], r["rows"], r["badge"]), (1, 2, 2))
        self.assertGreaterEqual(r["progress"], 1)

    def test_report_through_moves_the_tracker_and_the_counter(self):
        """4."""
        r = self.run_script("""
            feed([raw("Company %02d" % i) for i in range(1, 8)])
            api.jobs_prepare(5); wait()
            st = api.jobs_state()
            res = api.jobs_report_through(3)
            st2 = api.jobs_state()
            d = daily.load_daily()
            bad = api.jobs_report_through(9)
            worse = api.jobs_report_through("x")
            apps = api.apps()
            print(json.dumps({"rows": [r["company"] for r in st["rows"]], "res": res,
                              "status": [r["status"] for r in st2["rows"]], "unreported": st2["unreported"],
                              "count": daily.daily_state(d, d["items"][0], today_str())[1],
                              "tracked": sorted(a["company"] for a in apps["apps"]),
                              "sources": sorted({e["source"] for a in apps["apps"] for e in a["events"]}),
                              "bad": bad, "worse": worse, "badge": api.state()["jobs_unreported"]}))
        """)
        self.assertEqual(len(r["rows"]), 5)
        self.assertEqual((r["res"]["reported"], r["res"]["counted"], r["res"]["already_tracked"]), (3, 3, 0))
        self.assertEqual(r["res"]["daily"], {"title": "Apply for 20 simplifyjob positions", "count": 3, "target": 20})
        self.assertEqual(r["res"]["remaining"], [4, 5])
        self.assertEqual(r["status"], ["applied"] * 3 + ["shown"] * 2)
        self.assertEqual((r["unreported"], r["badge"], r["count"]), ([4, 5], 2, 3))
        self.assertEqual(r["tracked"], sorted(r["rows"][:3]))
        self.assertEqual(r["sources"], ["reported"])
        self.assertIn("outside this batch", r["bad"]["error"])
        self.assertIn("error", r["worse"])

    def test_verify_without_a_key_is_unverified_and_stays_offline(self):
        """5. urlopen is closed off in the prelude, so a fetch here would surface as an error."""
        r = self.run_script("""
            os.environ.pop(batch.KEY_NAME, None)
            feed([raw("Alder")])
            api.jobs_prepare(20); wait()
            started = api.jobs_verify(1)
            wait()
            out = last()
            row = api.jobs_state()["rows"][0]
            nope = api.jobs_verify("first")
            print(json.dumps({"started": started, "out": out, "stored": row["detail"]["verdict"], "nope": nope,
                              "verifier": api.jobs_state()["verifier"]}))
        """)
        self.assertEqual(r["started"], {"started": True})
        out = r["out"]
        self.assertEqual((out["kind"], out["position"], out["done"]), ("verify", 1, True))
        self.assertNotIn("errors", out)
        self.assertIsNone(out["verdict"]["eligible"])
        self.assertIn("TODAY_LLM_KEY", out["verdict"]["summary"])
        self.assertIsNone(r["stored"]["eligible"])
        self.assertEqual(r["verifier"], "none")
        self.assertIn("error", r["nope"])

    def test_skip_close_and_why(self):
        r = self.run_script("""
            tr = tracker.load(APP_DIR)
            tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "CTC",
                                       "role": "Software Engineer Intern", "title": "Thanks", "account": "me@gmail.com"}])
            tracker.save(APP_DIR, tr)
            feed([raw("Alder"), raw("Birch"), raw("Cedar"), raw("Chicago Trading Company")])
            api.jobs_prepare(20); wait()
            st = api.jobs_state()
            skipped = api.jobs_skip([1])
            why = api.jobs_why("Chicago Trading")
            closed = api.jobs_close_batch()
            empty = api.jobs_why("  ")
            no_batch = api.jobs_report_through(1)
            print(json.dumps({"rows": [r["company"] for r in st["rows"]], "skipped": skipped, "why": why,
                              "closed": closed, "after": api.jobs_state(), "empty": empty, "no_batch": no_batch}))
        """)
        self.assertEqual(sorted(r["rows"]), ["Alder", "Birch", "Cedar"])
        self.assertEqual(r["skipped"], {"skipped": [1]})
        self.assertEqual(r["closed"], {"skipped": [2, 3]})
        self.assertEqual((r["after"]["batch_id"], r["after"]["rows"]), (None, []))
        a = r["why"]["answers"][0]
        self.assertEqual((a["status"], a["reasons"][0]["code"]), ("excluded", "applied"))
        self.assertIn("error", r["empty"])
        self.assertIn("no open batch", r["no_batch"]["error"])

    def test_every_field_the_tab_reads_is_in_the_payload(self):
        """The Jobs tab is the one view whose status lines are driven entirely by `jobs_state`. A field the
        page reads and the Api does not send is silent: the warning simply never appears, which is how a
        missing exclusion seed went unreported. Checked mechanically so a new field cannot drift."""
        r = self.run_script("""
            keys = sorted(api.jobs_state())
            feed([raw("Alder")])
            api.jobs_prepare(20); wait()
            print(json.dumps({"empty": keys, "ready": sorted(api.jobs_state()),
                              "js": open(os.path.join(G.WEB_DIR, "app.js"), encoding="utf-8").read()}))
        """)
        js = r["js"]
        start = js.index("/* ---------- jobs ---------- */")
        end = js.index("/* ---------- mail accounts ---------- */")
        read = set(re.findall(r"\bj\.([A-Za-z_][A-Za-z0-9_]*)", js[start:end]))
        self.assertIn("seed_problem", read)               # the fix this test exists for
        self.assertIn("seed_imported", read)
        self.assertTrue(read)
        # Both shapes of the payload must carry every one of them: the tab renders before the first prepare.
        for where in ("empty", "ready"):
            self.assertEqual(sorted(read - set(r[where])), [], "%s payload is missing these" % where)
