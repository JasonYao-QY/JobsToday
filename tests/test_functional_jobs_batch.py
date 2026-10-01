"""Functional: jobs/batch.py through the real tracker, report and daily counter, against real files in the
throwaway data folder. The feed is a file written into the cache; nothing here touches the network or an API.
"""
import json
import os
import shutil
import unittest
import urllib.error

import daily
import env
import tracker
from store import APP_DIR
from timeutil import today_str

from jobs import batch, state
from tests._jobs_support import FEED_FIXTURE

DAY = 86400
CUTOFF = batch._cutoff_epoch(batch.DEFAULT_CUTOFF)
AFTER = CUTOFF + 2 * DAY            # a post date he has not screened
BEFORE = CUTOFF - 30 * DAY          # backlog
NOW = CUTOFF + 7 * DAY


def raw(company, title="Software Engineer Intern", locs=("NYC",), posted=AFTER, rid=None, **kw):
    """One row in the upstream feed's own format."""
    r = {"id": rid or ("%s-%s-%s" % (company, title, "-".join(locs))).replace(" ", "_"),
         "source": "Simplify", "category": "Software", "company_name": company, "title": title,
         "active": True, "is_visible": True, "terms": ["Summer 2027"], "date_posted": posted,
         "date_updated": posted, "url": "https://example.com/%s/%s" % (company.replace(" ", ""), title.replace(" ", "-")),
         "locations": list(locs), "sponsorship": "Other", "degrees": ["Bachelor's", "Master's"]}
    r.update(kw)
    return r


class BatchTest(unittest.TestCase):
    """Each test starts from an empty tracker, an empty jobs-data folder and a fresh applications counter."""

    def setUp(self):
        for name in ("applications.json", "daily.json"):
            try:
                os.remove(os.path.join(APP_DIR, name))
            except OSError:
                pass
        shutil.rmtree(batch.DATA_DIR, ignore_errors=True)
        self._key = os.environ.pop(batch.KEY_NAME, None)
        self.addCleanup(self._restore_key)
        d = daily.load_daily()
        d["items"] = [{"id": "d-apply", "title": "Apply for 20 simplifyjob positions", "target": 20}]
        daily.save_daily(d)
        self.conn = batch.connect()
        self.addCleanup(self.conn.close)

    def _restore_key(self):
        os.environ.pop(batch.KEY_NAME, None)
        if self._key is not None:
            os.environ[batch.KEY_NAME] = self._key

    def feed(self, rows):
        os.makedirs(batch.CACHE_DIR, exist_ok=True)
        with open(os.path.join(batch.CACHE_DIR, "simplify.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f)

    def prepare(self, n=20, now=NOW):
        return batch.prepare(self.conn, n=n, fetch=False, now=now)

    def counter(self):
        d = daily.load_daily()
        return daily.daily_state(d, d["items"][0], today_str())[1]

    def apps(self):
        return tracker.load(APP_DIR)["apps"]


def companies(rows):
    return [r["company"] for r in rows]


class BeforeFirstRun(unittest.TestCase):

    def setUp(self):
        shutil.rmtree(batch.DATA_DIR, ignore_errors=True)

    def test_status_is_renderable_and_creates_nothing(self):
        st = batch.status()
        self.assertEqual((st["ready"], st["batch_id"], st["rows"], st["unreported"]), (False, None, [], []))
        self.assertEqual(st["cutoff_date"], "2026-09-24")
        self.assertEqual(st["key_name"], "TODAY_LLM_KEY")
        self.assertFalse(st["feed"]["cached"])
        self.assertEqual(batch.unreported_count(), 0)
        self.assertFalse(os.path.exists(batch.DATA_DIR))


class Status(BatchTest):

    def test_status_follows_the_batch(self):
        self.feed([raw("Alder"), raw("Birch"), raw("Cedar")])
        out = self.prepare(n=20)
        st = batch.status(now=NOW)
        self.assertTrue(st["ready"])
        self.assertEqual(st["batch_id"], out["batch_id"])
        self.assertEqual([r["company"] for r in st["rows"]], companies(out["rows"]))
        self.assertEqual(st["unreported"], [1, 2, 3])
        self.assertEqual(batch.unreported_count(), 3)
        self.assertEqual((st["last_prepare"]["requested"], st["last_prepare"]["found"],
                          st["last_prepare"]["added"]), (20, 3, 3))
        self.assertEqual(st["cutoff"], CUTOFF)
        batch.report_through(self.conn, 2)
        self.assertEqual(batch.status(now=NOW)["unreported"], [3])
        self.assertEqual(batch.unreported_count(), 1)
        batch.close_batch(self.conn)
        st = batch.status(now=NOW)
        self.assertEqual((st["batch_id"], st["rows"], batch.unreported_count()), (None, [], 0))
        self.assertIsNotNone(st["last_batch"]["closed_at"])

    def test_status_does_not_touch_the_database(self):
        self.feed([raw("Alder")])
        self.prepare()
        self.conn.close()
        before = [os.stat(p).st_mtime_ns for p in batch.WATCH_PATHS if os.path.exists(p)]
        for _ in range(3):
            batch.status()
            batch.unreported_count()
        after = [os.stat(p).st_mtime_ns for p in batch.WATCH_PATHS if os.path.exists(p)]
        self.assertEqual(before, after)

    def test_stale_seed_and_feed_error_are_reported(self):
        os.makedirs(batch.DATA_DIR, exist_ok=True)
        with open(batch.SEED_PATH, "w") as f:
            json.dump({"taken": "2026-08-01", "exclusions": [{"company": "Atlassian", "kind": "banned"}]}, f)
        self.feed([raw("Alder")])

        def offline(req, timeout=None):
            raise urllib.error.URLError("offline")
        batch.prepare(self.conn, fetch=True, now=NOW, opener=offline)
        st = batch.status(now=NOW)
        self.assertTrue(st["seed_stale"])
        self.assertEqual(st["seed_age"]["days"], 61)
        self.assertIn("URLError", st["last_prepare"]["feed_error"])
        self.assertTrue(st["feed"]["cached"])


class Assembly(BatchTest):

    def test_unselected_candidates_come_back(self):
        """1. A batch of 1 out of 3, then a second batch, offers the other two."""
        self.feed([raw("Alder"), raw("Birch"), raw("Cedar")])
        first = self.prepare(n=1)
        self.assertEqual((first["found"], first["added"], len(first["rows"])), (3, 1, 1))
        batch.report_through(self.conn, 1)
        second = self.prepare(n=20)
        self.assertEqual(sorted(companies(second["rows"])),
                         sorted(set(["Alder", "Birch", "Cedar"]) - set(companies(first["rows"]))))
        self.assertEqual(state.watermark(self.conn), CUTOFF)          # never advanced

    def test_first_run_sets_the_cutoff_and_hides_the_backlog(self):
        """5. No watermark set: prepare sets it, and the backlog is not offered."""
        self.assertEqual(state.watermark(self.conn), 0)
        self.feed([raw("Oldco", posted=BEFORE), raw("Newco", posted=AFTER)])
        out = self.prepare()
        self.assertEqual(state.watermark(self.conn), CUTOFF)
        self.assertEqual(out["cutoff"], CUTOFF)
        self.assertEqual(companies(out["rows"]), ["Newco"])

    def test_cutoff_is_the_end_of_that_day_in_utc(self):
        day = batch._cutoff_epoch("2026-09-24")
        self.assertEqual(day % DAY, DAY - 1)
        self.feed([raw("OnTheDay", posted=day - (DAY - 1)),            # 2026-09-24 00:00 UTC
                   raw("DayAfter", posted=day + 1)])                   # 2026-09-25 00:00 UTC
        self.assertEqual(companies(self.prepare()["rows"]), ["DayAfter"])

    def test_real_feed_slice_is_all_backlog(self):
        with open(FEED_FIXTURE, "rb") as f:
            rows = json.load(f)
        self.feed(rows)
        out = self.prepare()
        self.assertEqual(out["rows"], [])
        self.assertIsNone(out["batch_id"])
        self.assertTrue(out["short"])

    def test_short_batch_is_not_padded(self):
        """7. Fewer eligible than requested: what exists, both numbers, no padding."""
        self.feed([raw("Alder"), raw("Birch"), raw("Oldco", posted=BEFORE),
                   raw("PhdCo", degrees=["PhD"]), raw("Londonco", locs=("London, UK",))])
        out = self.prepare(n=20)
        self.assertEqual((out["requested"], out["found"], len(out["rows"])), (20, 2, 2))
        self.assertTrue(out["short"])
        self.assertEqual(sorted(companies(out["rows"])), ["Alder", "Birch"])
        self.assertEqual(sorted((d["company"], d["code"]) for d in out["dropped"]),
                         [("Londonco", "non_us"), ("PhdCo", "degree")])
        for d in out["dropped"]:
            self.assertTrue(d["text"].endswith("."))

    def test_asking_again_with_nothing_new_makes_no_new_batch(self):
        self.feed([raw("Alder"), raw("Birch")])
        a = self.prepare()
        b = self.prepare()
        self.assertTrue(a["created"])
        self.assertFalse(b["created"])
        self.assertEqual(a["batch_id"], b["batch_id"])
        self.assertEqual(companies(a["rows"]), companies(b["rows"]))

    def test_rows_carry_what_the_tab_needs(self):
        self.feed([raw("Optiver", locs=("Austin, TX", "Chicago, IL"))])
        r = self.prepare()["rows"][0]
        self.assertEqual((r["position"], r["company"], r["city"], r["resume"], r["status"]),
                         (1, "Optiver", "Chicago, IL", "SWE-fin", "shown"))
        self.assertTrue(r["url"].startswith("https://example.com/Optiver/"))
        self.assertEqual(r["detail"]["score"], sum(p["points"] for p in r["detail"]["parts"]))
        self.assertIn("texas", [f["code"] for f in r["detail"]["flags"]])

    def test_failed_fetch_falls_back_to_the_cache(self):
        self.feed([raw("Alder")])

        def offline(req, timeout=None):
            raise urllib.error.URLError("offline")
        out = batch.prepare(self.conn, n=20, fetch=True, now=NOW, opener=offline)
        self.assertIn("URLError", out["feed"]["error"])
        self.assertEqual(companies(out["rows"]), ["Alder"])


class Exclusion(BatchTest):

    def test_application_under_another_spelling_is_excluded(self):
        """4. A company in applications.json under a different spelling, through the real path."""
        tr = tracker.load(APP_DIR)
        tracker.ingest_items(tr, [
            {"key": "a:m1", "date": today_str(), "kind": "applied", "company": "JPMorganChase",
             "role": "Software Engineer Intern", "title": "Thank you for applying", "account": "me@gmail.com"},
            {"key": "a:m2", "date": today_str(), "kind": "applied", "company": "CTC",
             "role": "Software Engineer Intern", "title": "Application received", "account": "me@gmail.com"}])
        tracker.save(APP_DIR, tr)
        self.feed([raw("JPMorgan Chase", title="Software Engineer Intern - Summer 2027"),
                   raw("JPMorgan Chase", title="Quantitative Research Intern"),
                   raw("Chicago Trading Company", title="Software Engineering Intern"),
                   raw("Alder")])
        out = self.prepare()
        self.assertEqual(sorted((r["company"], r["title"]) for r in out["rows"]),
                         [("Alder", "Software Engineer Intern"),
                          ("JPMorgan Chase", "Quantitative Research Intern")])   # a different role stays
        self.assertEqual(sorted((d["company"], d["code"]) for d in out["dropped"]),
                         [("Chicago Trading Company", "applied"), ("JPMorgan Chase", "applied")])

    def test_two_spellings_of_one_company_merge_into_one_row(self):
        self.feed([raw("JPMorgan Chase", locs=("NYC",), rid="a"),
                   raw("JPMorganChase", locs=("Chicago, IL",), rid="b")])
        out = self.prepare()
        self.assertEqual(len(out["rows"]), 1)

    def test_seed_is_imported_and_its_age_reported(self):
        os.makedirs(batch.DATA_DIR, exist_ok=True)
        with open(batch.SEED_PATH, "w") as f:
            json.dump({"taken": "2026-09-28", "exclusions": [{"company": "Atlassian", "kind": "banned"}]}, f)
        self.feed([raw("Atlassian"), raw("Alder")])
        out = self.prepare()
        self.assertEqual(companies(out["rows"]), ["Alder"])
        self.assertEqual(out["dropped"][0]["code"], "banned")
        self.assertEqual(out["seed_age"]["taken"], "2026-09-28")
        self.assertEqual(out["seed_age"]["days"], 3)                    # NOW is 2026-10-01

    def test_a_different_role_at_the_same_company_is_offered_and_says_so(self):
        tr = tracker.load(APP_DIR)
        tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "DoorDash",
                                   "role": "Software Engineer Intern", "title": "Thanks", "account": "me@gmail.com"}])
        tracker.save(APP_DIR, tr)
        state.add_exclusion(self.conn, "Doordash", "pending", title="Data Science Intern")
        self.feed([raw("DoorDash", title="Machine Learning Engineer Intern - Masters"), raw("Alder")])
        rows = {r["company"]: r for r in self.prepare()["rows"]}
        self.assertEqual([(x["kind"], x["title"]) for x in rows["DoorDash"]["same_company"]],
                         [("applied", "Software Engineer Intern"), ("pending", "Data Science Intern")])
        self.assertEqual(rows["DoorDash"]["same_company"][0]["text"],
                         "You already applied here for: Software Engineer Intern.")
        self.assertEqual(rows["Alder"]["same_company"], [])
        # Computed on read, never stored: the same answer comes back from status() and open_batch().
        self.assertNotIn("same_company", rows["DoorDash"]["detail"])
        for again in (batch.status(now=NOW)["rows"], batch.open_batch(self.conn)["rows"]):
            self.assertEqual({r["company"]: len(r["same_company"]) for r in again}, {"DoorDash": 2, "Alder": 0})

    def test_two_roles_at_one_company_in_the_same_batch_flag_each_other(self):
        """Both rows are on screen at once, which is when applying twice is easiest."""
        self.feed([raw("Acme", title="Software Engineer Intern", rid="a1"),
                   raw("Acme", title="Machine Learning Engineer Intern", rid="a2"), raw("Alder")])
        rows = self.prepare()["rows"]
        acme = [r for r in rows if r["company"] == "Acme"]
        self.assertEqual(len(acme), 2)
        for me, other in (acme, acme[::-1]):
            self.assertEqual([(x["kind"], x["position"], x["title"]) for x in me["same_company"]],
                             [("batch", other["position"], other["title"])])
            self.assertIn("Row %d in this batch" % other["position"], me["same_company"][0]["text"])
        self.assertEqual([r["same_company"] for r in rows if r["company"] == "Alder"], [[]])

    def test_the_other_open_row_is_never_cut_by_the_cap(self):
        tr = tracker.load(APP_DIR)
        roles = ["Data Engineer Intern", "Product Analyst Intern", "Hardware Test Intern", "Security Intern",
                 "Compiler Intern", "Robotics Controls Intern"]
        tracker.ingest_items(tr, [{"key": "a:m%d" % i, "date": today_str(), "kind": "applied", "company": "Acme",
                                   "role": role, "title": "Thanks", "account": "me@gmail.com"}
                                  for i, role in enumerate(roles)])
        tracker.save(APP_DIR, tr)
        self.feed([raw("Acme", title="Software Engineer Intern", rid="a1"),
                   raw("Acme", title="Machine Learning Engineer Intern", rid="a2")])
        rows = self.prepare()["rows"]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(len(r["same_company"]), 5)
            self.assertEqual(r["same_company"][0]["kind"], "batch")

    def test_reporting_one_row_shows_up_on_the_other_at_once(self):
        self.feed([raw("Acme", title="Software Engineer Intern", rid="a1"),
                   raw("Acme", title="Quantitative Research Intern", rid="a2", category="Quant")])
        rows = self.prepare()["rows"]
        first, second = rows[0], rows[1]
        self.assertEqual(second["same_company"][0]["kind"], "batch")
        batch.report_through(self.conn, 1)
        after = {r["position"]: r for r in batch.status(now=NOW)["rows"]}
        self.assertEqual(after[1]["same_company"], [])                       # applied: nothing left to warn about
        self.assertEqual([(x["kind"], x["title"]) for x in after[2]["same_company"]],
                         [("applied", first["title"])])                      # once, not also as a batch row
        # ...and it survives the carry-over into the next batch.
        self.feed([raw("Acme", title="Software Engineer Intern", rid="a1"),
                   raw("Acme", title="Quantitative Research Intern", rid="a2", category="Quant"), raw("Alder")])
        nxt = self.prepare(now=NOW + DAY)["rows"]
        self.assertEqual((nxt[0]["company"], nxt[0]["title"]), ("Acme", second["title"]))
        self.assertEqual([(x["kind"], x["title"]) for x in nxt[0]["same_company"]], [("applied", first["title"])])

    def write_seed(self, taken, entries):
        os.makedirs(batch.DATA_DIR, exist_ok=True)
        with open(batch.SEED_PATH, "w") as f:
            json.dump({"taken": taken, "exclusions": entries}, f)

    def test_a_replaced_seed_takes_effect_on_the_next_prepare(self):
        """The review's bug: the seed was read by the first prepare and never again, so replacing the file,
        which is what the tab tells him to do, changed nothing and the stale warning could not be cleared."""
        self.write_seed("2026-09-01", [{"company": "Alder", "kind": "banned"}])
        self.feed([raw("Alder"), raw("Birch"), raw("Cedar"), raw("Dogwood")])
        first = self.prepare(n=1)
        self.assertEqual(first["seed_age"], {"taken": "2026-09-01", "days": 30})
        self.assertTrue(batch.status(now=NOW)["seed_stale"])
        self.assertEqual(state.watermark(self.conn), CUTOFF)
        batch.close_batch(self.conn)

        banned = sorted(set(["Birch", "Cedar", "Dogwood"]) - set(companies(first["rows"])))[0]
        self.write_seed("2026-10-01", [{"company": "Alder", "kind": "banned"},
                                       {"company": banned, "kind": "banned"}])
        second = self.prepare(n=20)
        self.assertNotIn(banned, companies(second["rows"]))                 # the new entry excludes
        self.assertIn((banned, "banned"), [(d["company"], d["code"]) for d in second["dropped"]])
        self.assertEqual(second["seed_age"], {"taken": "2026-10-01", "days": 0})    # and the date moved
        self.assertFalse(batch.status(now=NOW)["seed_stale"])
        self.assertEqual(state.watermark(self.conn), CUTOFF)                # the cutoff did not

        # An entry taken out of the file stops excluding.
        self.write_seed("2026-10-01", [{"company": banned, "kind": "banned"}])
        batch.close_batch(self.conn)
        third = self.prepare(n=20)
        self.assertEqual(companies(third["rows"]), ["Alder"])

    def test_roleless_application_closes_the_company(self):
        tr = tracker.load(APP_DIR)
        tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "Robinhood",
                                   "role": "", "title": "Thanks", "account": "me@gmail.com"}])
        tracker.save(APP_DIR, tr)
        self.feed([raw("Robinhood", title="Machine Learning Engineer Intern"), raw("Alder")])
        out = self.prepare()
        self.assertEqual(companies(out["rows"]), ["Alder"])
        # ...and says why, and what to do about it, instead of a bare "already applied".
        self.assertIn("did not name the role", out["dropped"][0]["text"])
        self.assertIn("Applications tab", batch.why_dropped(self.conn, "Robinhood")[0]["text"])


class Reporting(BatchTest):

    def twenty(self):
        self.feed([raw("Company %02d" % i) for i in range(1, 26)])
        out = self.prepare(n=20)
        self.assertEqual(len(out["rows"]), 20)
        return out

    def test_applied_through_13_and_the_rest_carry_over(self):
        """2. 13 tracker records, counter +13, 14-20 still open, and the next batch starts with those seven."""
        first = self.twenty()
        res = batch.report_through(self.conn, 13)
        self.assertEqual((res["reported"], res["counted"], res["already_tracked"]), (13, 13, 0))
        self.assertEqual(res["daily"], {"title": "Apply for 20 simplifyjob positions", "count": 13, "target": 20})
        self.assertEqual(res["remaining"], list(range(14, 21)))
        self.assertEqual(len(self.apps()), 13)
        self.assertEqual(self.counter(), 13)
        self.assertEqual(sorted(tracker.app_company(a) for a in self.apps()),
                         sorted(companies(first["rows"][:13])))

        still = batch.open_batch(self.conn)
        self.assertEqual(still["batch_id"], first["batch_id"])
        self.assertEqual(still["unreported"], list(range(14, 21)))

        second = self.prepare(n=20)
        self.assertTrue(second["created"])
        self.assertEqual(second["carried"], 7)
        self.assertEqual(companies(second["rows"][:7]), companies(first["rows"][13:]))   # same seven, same order
        self.assertEqual([r["detail"].get("carried_from") for r in second["rows"]],
                         [first["batch_id"]] * 7 + [None] * 5)
        self.assertEqual(len(second["rows"]), 12)                       # 7 carried + the 5 not yet shown
        self.assertEqual(len(set(companies(second["rows"]))), 12)
        self.assertFalse(set(companies(second["rows"])) & set(companies(first["rows"][:13])))
        old = state.batch_rows(self.conn, first["batch_id"])
        self.assertEqual([r["status"] for r in old], ["applied"] * 13 + ["carried"] * 7)
        self.assertEqual(batch.open_batch(self.conn)["batch_id"], second["batch_id"])

    def test_carried_rows_are_not_refiltered_or_reranked(self):
        self.feed([raw("Alder"), raw("Birch")])
        first = self.prepare()
        order = companies(first["rows"])
        # After the batch was made, something turns up that would now exclude row 2, and a far better
        # posting appears. Neither moves or removes what is already in front of him.
        state.add_exclusion(self.conn, order[1], "banned")
        self.feed([raw("Alder"), raw("Birch"),
                   raw("DoorDash", title="Machine Learning Engineer Intern - Masters", locs=("Seattle, WA",))])
        second = self.prepare(now=NOW + DAY)
        self.assertEqual(companies(second["rows"]), order + ["DoorDash"])

    def test_mail_found_it_first_still_counts(self):
        """3. An application the mail scan already found: counted, not new, counter still goes up."""
        self.feed([raw("Mistwall")])
        self.prepare()
        tr = tracker.load(APP_DIR)
        tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "Mistwall",
                                   "role": "Software Engineer Intern (2027) - NYC", "title": "Thank you",
                                   "account": "me@gmail.com"}])
        tracker.save(APP_DIR, tr)
        res = batch.report_through(self.conn, 1)
        self.assertEqual((res["reported"], res["counted"], res["already_tracked"]), (1, 1, 1))
        self.assertEqual(len(self.apps()), 1)
        self.assertEqual(self.counter(), 1)
        self.assertEqual(res["daily"]["count"], 1)

    def test_reporting_twice_counts_once(self):
        self.twenty()
        batch.report_through(self.conn, 5)
        res = batch.report_through(self.conn, 8)
        self.assertEqual((res["reported"], res["counted"]), (3, 3))     # only 6, 7, 8 were still open
        self.assertEqual(self.counter(), 8)
        self.assertEqual(len(self.apps()), 8)

    def test_full_company_string_reaches_the_tracker(self):
        self.feed([raw("Susquehanna International Group (SIG)", title="Quantitative Research Intern")])
        self.prepare()
        batch.report_through(self.conn, 1)
        self.assertEqual(tracker.app_company(self.apps()[0]), "Susquehanna International Group (SIG)")
        self.assertTrue(self.apps()[0]["events"][0]["link"].startswith("https://example.com/"))

    def test_skip_retires_and_close_skips_the_rest(self):
        first = self.twenty()
        batch.report_through(self.conn, 2)
        self.assertEqual(batch.skip(self.conn, [3, 4, 99]), {"skipped": [3, 4]})
        self.assertEqual(batch.close_batch(self.conn)["skipped"], list(range(5, 21)))
        self.assertIsNone(batch.open_batch(self.conn))
        second = self.prepare()
        self.assertEqual(second["carried"], 0)
        self.assertFalse(set(companies(second["rows"])) & set(companies(first["rows"])))
        self.assertEqual(self.counter(), 2)                             # skipping reports nothing

    def test_bad_position_and_no_batch(self):
        with self.assertRaises(ValueError):
            batch.report_through(self.conn, 1)
        self.feed([raw("Alder")])
        self.prepare()
        for p in (0, 2):
            with self.assertRaises(ValueError):
                batch.report_through(self.conn, p)
        self.assertEqual(batch.close_batch(self.conn)["skipped"], [1])
        self.assertEqual(batch.close_batch(self.conn), {"skipped": []})


LONG = ("Summer 2027 internship in New York for Master's students graduating December 2027. " * 8)


def page(text):
    return lambda url: {"url": url, "fetched": url, "kind": "html", "text": text,
                        "readable": True, "error": None}


def answer(**checks):
    full = {c: {"ok": True, "evidence": "ok"} for c in
            ("term", "degree", "graduation", "class_standing", "sponsorship", "citizenship", "location",
             "clearance")}
    full.update(checks)
    return {"content": [{"type": "text", "text": json.dumps({"checks": full, "pay": "$50/hr", "summary": "s"})}]}


class Verification(BatchTest):

    def test_no_key_still_assembles_and_verdicts_are_unknown(self):
        """6. No API key: the batch assembles, verdicts are unknown, nothing raises, nothing is fetched."""
        self.assertEqual(env.get(batch.KEY_NAME), "")
        self.feed([raw("Alder")])
        out = self.prepare()
        self.assertEqual(out["verifier"], "none")
        self.assertNotIn("verdict", out["rows"][0]["detail"])           # lazy: nothing verified up front

        def never(url):
            raise AssertionError("fetched a page with no key set")
        v = batch.verify_row(self.conn, 1, fetch=never)
        self.assertIsNone(v["eligible"])
        self.assertIn(batch.KEY_NAME, v["summary"])
        self.assertIsNone(batch.open_batch(self.conn)["rows"][0]["detail"]["verdict"]["eligible"])

    def test_with_a_key_one_row_is_verified_on_open_and_cached(self):
        os.environ[batch.KEY_NAME] = "sk-test-not-real"
        self.feed([raw("Alder"), raw("Birch")])
        out = self.prepare()
        self.assertEqual(out["verifier"], "llm")
        calls = []

        def transport(payload):
            calls.append(payload)
            return answer(citizenship={"ok": False, "evidence": "US citizens only"})
        v = batch.verify_row(self.conn, 1, fetch=page(LONG), transport=transport)
        self.assertIs(v["eligible"], False)
        self.assertEqual(v["pay"], "$50/hr")
        again = batch.verify_row(self.conn, 1, fetch=page(LONG), transport=transport)
        self.assertTrue(again["cached"])
        self.assertEqual(len(calls), 1)                                 # the second open cost nothing
        rows = batch.open_batch(self.conn)["rows"]
        self.assertIs(rows[0]["detail"]["verdict"]["eligible"], False)
        self.assertNotIn("verdict", rows[1]["detail"])                  # row 2 was never opened
        self.assertNotIn("sk-test-not-real", json.dumps(rows) + json.dumps(out) + json.dumps(calls))

    def test_verdict_survives_the_carry_over(self):
        os.environ[batch.KEY_NAME] = "sk-test-not-real"
        self.feed([raw("Alder")])
        self.prepare()
        batch.verify_row(self.conn, 1, fetch=page(LONG), transport=lambda p: answer())
        self.feed([raw("Alder"), raw("Birch")])
        second = self.prepare(now=NOW + DAY)
        self.assertIs(second["rows"][0]["detail"]["verdict"]["eligible"], True)


class WhyDropped(BatchTest):

    def test_every_kind_of_answer(self):
        tr = tracker.load(APP_DIR)
        tracker.ingest_items(tr, [{"key": "a:m1", "date": today_str(), "kind": "applied", "company": "Optiver",
                                   "role": "Software Engineer Intern", "title": "Thanks", "account": "me@gmail.com"}])
        tracker.save(APP_DIR, tr)
        self.feed([raw("Alder"), raw("Birch"), raw("Cedar"), raw("Optiver"), raw("Oldco", posted=BEFORE),
                   raw("PhdCo", degrees=["PhD"])])
        first = self.prepare(n=1)
        shown = first["rows"][0]["company"]
        waiting = sorted(set(["Alder", "Birch", "Cedar"]) - {shown})[0]

        def why(c):
            return batch.why_dropped(self.conn, c)[0]
        self.assertEqual(why(shown)["status"], "shown")
        self.assertIn("position 1", why(shown)["text"])
        self.assertEqual(why("Optiver")["status"], "excluded")
        self.assertEqual(why("Optiver")["reasons"][0]["code"], "applied")
        self.assertEqual(why("PhdCo")["reasons"][0]["code"], "degree")
        self.assertEqual(why("Oldco")["status"], "backlog")
        self.assertIn("2026-09-24", why("Oldco")["text"])
        self.assertEqual(why(waiting)["status"], "waiting")
        self.assertIn("of 2 waiting", why(waiting)["text"])
        self.assertEqual(why("Nowhere Inc")["status"], "not_in_feed")
        self.assertEqual(batch.why_dropped(self.conn, "  "), [])


if __name__ == "__main__":
    unittest.main()
