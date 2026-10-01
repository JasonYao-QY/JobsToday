"""jobs/state.py: the seen watermark, batches, exclusions, verdict cache."""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest

from jobs import state

DAY = 86400
T0 = 1790000000            # 2026-09-21, an arbitrary "upstream post" time


def L(key, *variants):
    """A listing with variants given as (id, posted)."""
    company, title = key.split("|")
    return {"key": key, "company": company, "title": title,
            "variants": [{"id": i, "company": company, "title": title, "posted": p}
                         for i, p in variants]}


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "jobs", "jobs.db")
        self.db = state.open_db(self.path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class Schema(Base):

    def test_wal_and_reopen(self):
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.db.close()
        self.db = state.open_db(self.path)
        self.assertEqual(state.watermark(self.db), 0)

    def test_newer_schema_refuses(self):
        self.db.execute("UPDATE meta SET v='99' WHERE k='schema'")
        self.db.close()
        with self.assertRaises(RuntimeError):
            state.open_db(self.path)
        self.db = sqlite3.connect(":memory:")


class Watermark(Base):

    def test_first_sync_dates_from_upstream_not_now(self):
        old = L("acme|swe", ("a1", T0 - 60 * DAY))
        recent = L("beta|swe", ("b1", T0 - DAY))
        state.sync_seen(self.db, [old, recent], now=T0)
        state.set_watermark(self.db, T0 - 7 * DAY)   # "I screened up to a week ago"
        fresh = state.new_since(self.db, [old, recent])
        self.assertEqual([x["key"] for x in fresh], ["beta|swe"])

    def test_later_syncs_date_from_now(self):
        state.sync_seen(self.db, [L("acme|swe", ("a1", T0))], now=T0)
        state.set_watermark(self.db, T0 + DAY)
        # Posted long ago upstream, but we only just met it: it is new to him.
        late = L("gamma|swe", ("g1", T0 - 90 * DAY))
        n = state.sync_seen(self.db, [L("acme|swe", ("a1", T0)), late], now=T0 + 2 * DAY)
        self.assertEqual(n, 1)
        self.assertEqual([x["key"] for x in state.new_since(self.db, [late])], ["gamma|swe"])

    def test_first_seen_never_moves(self):
        state.sync_seen(self.db, [L("acme|swe", ("a1", T0))], now=T0)
        state.sync_seen(self.db, [L("acme|swe", ("a1", T0))], now=T0 + 5 * DAY)
        row = self.db.execute("SELECT first_seen, last_seen FROM variants").fetchone()
        self.assertEqual((row[0], row[1]), (T0, T0 + 5 * DAY))

    def test_new_city_row_makes_an_old_listing_new_again(self):
        state.sync_seen(self.db, [L("opt|swe", ("chi", T0))], now=T0)
        state.set_watermark(self.db, T0 + DAY)
        both = L("opt|swe", ("chi", T0), ("nyc", T0 + 2 * DAY))
        state.sync_seen(self.db, [both], now=T0 + 2 * DAY)
        self.assertEqual(len(state.new_since(self.db, [both])), 1)

    def test_shown_is_never_new_again(self):
        x = L("acme|swe", ("a1", T0))
        state.sync_seen(self.db, [x], now=T0)
        state.create_batch(self.db, [row(x)], requested=20, now=T0 + 1)
        state.sync_seen(self.db, [L("acme|swe", ("a1", T0), ("a2", T0))], now=T0 + DAY)
        self.assertEqual(state.new_since(self.db, [x], since=0), [])


def row(x, city="NYC"):
    v = x["variants"][0]
    return {"listing_key": x["key"], "variant_id": v["id"], "company": x["company"],
            "title": x["title"], "city": city, "url": "https://x/" + v["id"],
            "resume": "SWE", "detail": {"score": 1.0}}


class Batches(Base):

    def test_create_numbers_from_one_and_leaves_the_cutoff_alone(self):
        state.set_watermark(self.db, T0 - DAY)
        xs = [L("a|swe", ("a1", T0)), L("b|swe", ("b1", T0)), L("c|swe", ("c1", T0))]
        bid = state.create_batch(self.db, [row(x) for x in xs], requested=20, now=T0 + 5)
        self.assertEqual(state.watermark(self.db), T0 - DAY)      # not advanced
        rows = state.batch_rows(self.db, bid)
        self.assertEqual([r["position"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["detail"], {"score": 1.0})
        self.assertEqual(state.last_batch(self.db)["since"], T0 - DAY)   # history kept

    def test_unselected_listing_is_offered_again(self):
        # The bug the review found: a batch of one out of three eligible
        # listings used to retire the other two for good.
        xs = [L("a|swe", ("a1", T0)), L("b|swe", ("b1", T0)), L("c|swe", ("c1", T0))]
        state.sync_seen(self.db, xs, now=T0)
        state.set_watermark(self.db, T0 - DAY)
        self.assertEqual([x["key"] for x in state.new_since(self.db, xs)],
                         ["a|swe", "b|swe", "c|swe"])
        state.create_batch(self.db, [row(xs[0])], requested=1, now=T0 + 10)
        self.assertEqual([x["key"] for x in state.new_since(self.db, xs)],
                         ["b|swe", "c|swe"])
        # New postings still arrive, and the backlog cutoff still holds.
        d = L("d|swe", ("d1", T0 + DAY))
        old = L("e|swe", ("e1", T0 - 30 * DAY))
        state.sync_seen(self.db, xs + [d], now=T0 + DAY)
        self.assertEqual([x["key"] for x in state.new_since(self.db, xs + [d])],
                         ["b|swe", "c|swe", "d|swe"])

    def test_backlog_cutoff_still_suppresses_old_rows(self):
        old = L("old|swe", ("o1", T0 - 60 * DAY))
        state.sync_seen(self.db, [old], now=T0)          # bootstrap: dated by post date
        state.set_watermark(self.db, T0 - 7 * DAY)
        state.create_batch(self.db, [], requested=20, now=T0 + 1)
        self.assertEqual(state.new_since(self.db, [old]), [])

    def test_mark_and_close(self):
        xs = [L("a|swe", ("a1", T0)), L("b|swe", ("b1", T0))]
        bid = state.create_batch(self.db, [row(x) for x in xs], requested=2, now=T0)
        state.mark(self.db, bid, [1], "applied", app_ids={1: "app-1"})
        self.assertIsNone(state.last_batch(self.db)["closed_at"])
        state.mark(self.db, bid, [2], "skipped")
        self.assertIsNotNone(state.last_batch(self.db)["closed_at"])
        r = state.batch_rows(self.db, bid)
        self.assertEqual([(x["status"], x["app_id"]) for x in r],
                         [("applied", "app-1"), ("skipped", None)])
        with self.assertRaises(ValueError):
            state.mark(self.db, bid, [1], "maybe")

    def test_open_batch_and_carried(self):
        xs = [L("a|swe", ("a1", T0)), L("b|swe", ("b1", T0))]
        self.assertIsNone(state.open_batch(self.db))
        bid = state.create_batch(self.db, [row(x) for x in xs], requested=2, now=T0)
        self.assertEqual(state.open_batch(self.db)["id"], bid)
        state.mark(self.db, bid, [1], "applied")
        self.assertEqual(state.open_batch(self.db)["id"], bid)       # row 2 still unreported
        state.mark(self.db, bid, [2], "carried")
        self.assertIsNone(state.open_batch(self.db))
        self.assertIsNotNone(state.last_batch(self.db)["closed_at"])

    def test_row_detail_merge(self):
        bid = state.create_batch(self.db, [row(L("a|swe", ("a1", T0)))], requested=1, now=T0)
        state.set_row_detail(self.db, bid, 1, "verdict", {"eligible": None})
        d = state.batch_rows(self.db, bid)[0]["detail"]
        self.assertEqual(d, {"score": 1.0, "verdict": {"eligible": None}})
        with self.assertRaises(KeyError):
            state.set_row_detail(self.db, bid, 9, "verdict", {})

    def test_history_and_first_seen(self):
        x = L("a|swe", ("a1", T0))
        state.sync_seen(self.db, [x], now=T0)
        bid = state.create_batch(self.db, [row(x)], requested=1, now=T0)
        h = state.shown_history(self.db, ["a|swe", "zzz"])
        self.assertEqual([(r["batch_id"], r["position"], r["status"]) for r in h], [(bid, 1, "shown")])
        self.assertEqual(state.first_seen(self.db, ["a1", "nope"]), {"a1": T0})
        self.assertEqual(state.shown_history(self.db, []), [])

    def test_failed_batch_writes_nothing(self):
        bad = [row(L("a|swe", ("a1", T0))), {"listing_key": "b"}]   # missing fields
        with self.assertRaises(KeyError):
            state.create_batch(self.db, bad, requested=2, now=T0)
        self.assertIsNone(state.last_batch(self.db))
        self.assertEqual(state.watermark(self.db), 0)


class Exclusions(Base):

    def test_union(self):
        state.add_exclusion(self.db, "Atlassian", "banned")
        state.add_exclusion(self.db, "Atlassian", "banned")          # idempotent
        x = L("zipline|perception", ("z1", T0))
        state.create_batch(self.db, [row(x)], requested=20, now=T0)
        apps = [{"company": "JPMorgan Chase", "role": "Data & AI Analyst", "status": "rejected"}]
        u = state.exclusion_union(self.db, apps)
        kinds = sorted((e["company"], e["kind"]) for e in u)
        self.assertEqual(kinds, [("Atlassian", "banned"), ("JPMorgan Chase", "applied"),
                                 ("zipline", "pending")])

    def test_reported_positions_leave_pending(self):
        x = L("zipline|perception", ("z1", T0))
        bid = state.create_batch(self.db, [row(x)], requested=20, now=T0)
        state.mark(self.db, bid, [1], "skipped")
        self.assertEqual(state.exclusion_union(self.db), [])

    def test_import_seed(self):
        p = os.path.join(self.dir, "seed.json")
        with open(p, "w") as f:
            json.dump({"taken": "2026-10-01", "exclusions": [
                {"company": "Google", "kind": "capped"},
                {"company": "Optiver", "title": "SWE Intern", "kind": "applied"},
                {"company": "X", "kind": "nonsense"},
                "junk",
            ]}, f)
        self.assertEqual(state.import_exclusions(self.db, p), 2)
        self.assertEqual(state.import_exclusions(self.db, p), 2)
        self.assertEqual(len(state.list_exclusions(self.db)), 2)
        self.assertEqual(state.import_exclusions(self.db, p + ".missing"), 0)
        self.assertEqual(state.seed_info(self.db)["taken"], "2026-10-01")
        self.assertIsNotNone(state.seed_info(self.db)["imported"])

    def seed(self, taken, entries):
        p = os.path.join(self.dir, "seed.json")
        with open(p, "w") as f:
            json.dump({"taken": taken, "exclusions": entries}, f)
        return p

    def test_the_seed_file_is_the_list(self):
        state.add_exclusion(self.db, "Handmade", "banned")                 # not from the seed
        p = self.seed("2026-09-01", [{"company": "Atlassian", "kind": "banned"},
                                     {"company": "Zipline", "title": "Perception Intern", "kind": "pending",
                                      "note": "batch B"}])
        state.import_exclusions(self.db, p)
        p = self.seed("2026-10-01", [{"company": "Atlassian", "kind": "banned"},
                                     {"company": "Vanguard", "kind": "banned"},
                                     {"company": "Zipline", "title": "Perception Intern", "kind": "pending",
                                      "note": "still open"}])
        self.assertEqual(state.import_exclusions(self.db, p), 3)
        got = {(e["company"], e["kind"], e["note"], e["origin"]) for e in state.list_exclusions(self.db)}
        self.assertEqual(got, {("Atlassian", "banned", None, "seed"), ("Vanguard", "banned", None, "seed"),
                               ("Zipline", "pending", "still open", "seed"),      # edited note picked up
                               ("Handmade", "banned", None, "manual")})
        self.assertEqual(state.seed_info(self.db)["taken"], "2026-10-01")
        # Dropping an entry from the file drops it here; the hand-made one stays.
        state.import_exclusions(self.db, self.seed("2026-10-02", [{"company": "Vanguard", "kind": "banned"}]))
        self.assertEqual(sorted(e["company"] for e in state.list_exclusions(self.db)), ["Handmade", "Vanguard"])

    def test_an_unreadable_seed_removes_nothing(self):
        p = self.seed("2026-09-01", [{"company": "Atlassian", "kind": "banned"}])
        state.import_exclusions(self.db, p)
        for bad in ("{not json", json.dumps({"taken": "2026-10-01"}), json.dumps({"exclusions": "nope"})):
            with open(p, "w") as f:
                f.write(bad)
            self.assertEqual(state.import_exclusions(self.db, p), 0)
        self.assertEqual(state.import_exclusions(self.db, p + ".gone"), 0)
        self.assertEqual([e["company"] for e in state.list_exclusions(self.db)], ["Atlassian"])
        self.assertEqual(state.seed_info(self.db)["taken"], "2026-09-01")
        # ...but a readable file with an empty list is a decision, and clears the seed entries.
        self.assertEqual(state.import_exclusions(self.db, self.seed("2026-10-03", [])), 0)
        self.assertEqual(state.list_exclusions(self.db), [])

    def test_remove(self):
        state.add_exclusion(self.db, "Vanguard", "banned")
        eid = state.list_exclusions(self.db)[0]["id"]
        state.remove_exclusion(self.db, eid)
        self.assertEqual(state.list_exclusions(self.db), [])


class Verdicts(Base):

    def test_roundtrip_keyed_by_page_and_verifier(self):
        self.assertIsNone(state.get_verdict(self.db, "h1", "null/1"))
        state.put_verdict(self.db, "h1", "null/1", {"eligible": True})
        state.put_verdict(self.db, "h1", "llm/1", {"eligible": False})
        self.assertEqual(state.get_verdict(self.db, "h1", "null/1"), {"eligible": True})
        self.assertEqual(state.get_verdict(self.db, "h1", "llm/1"), {"eligible": False})
        self.assertIsNone(state.get_verdict(self.db, "h2", "null/1"))


class Concurrency(Base):

    def test_background_writer_and_foreground_reader(self):
        xs = [L("c%d|swe" % i, ("v%d" % i, T0)) for i in range(300)]
        errors = []

        def bg():
            try:
                conn = state.open_db(self.path)
                for i in range(0, 300, 30):
                    state.sync_seen(conn, xs[i:i + 30], now=T0 + i)
                conn.close()
            except Exception as e:      # pragma: no cover
                errors.append(e)
        t = threading.Thread(target=bg)
        t.start()
        for _ in range(50):
            state.add_exclusion(self.db, "Vanguard", "banned")
            self.db.execute("SELECT COUNT(*) FROM variants").fetchone()
        t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM variants").fetchone()[0], 300)


if __name__ == "__main__":
    unittest.main()
