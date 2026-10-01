"""jobs/listings.py against a saved slice of the real SimplifyJobs feed.

The fixture is 18 real rows taken from the feed on 2026-10-01, chosen for
the shapes that broke the first top-20 attempt (duplicate-title rows,
multi-city rows with non-US cities) plus two malformed rows. No network.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error

from jobs import listings

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "simplify_slice.json")


def _raw():
    with open(FIXTURE, "rb") as f:
        return f.read()


class NormalizeLocation(unittest.TestCase):

    def check(self, raw, **want):
        got = listings.normalize_location(raw)
        for k, v in want.items():
            self.assertEqual(got[k], v, "%r: %s" % (raw, k))

    def test_simplify_shorthand(self):
        self.check("SF", city="San Francisco", state="CA", us=True, metro="bay_area")
        self.check("NYC", city="New York", state="NY", us=True, metro="nyc")
        self.check("South SF", city="South San Francisco", metro="bay_area")
        self.check("LA", city="Los Angeles", state="CA", us=True, metro=None)

    def test_city_state(self):
        self.check("Chicago, IL", us=True, metro="chicago")
        self.check("Austin, TX", state="TX", us=True, metro=None)
        self.check("Bellevue, WA", metro="seattle")

    def test_washington_the_state_is_not_dc(self):
        self.check("Washington", state="WA", city=None, us=True)
        self.check("Washington, DC", state="DC", us=True)

    def test_state_only(self):
        self.check("Texas", state="TX", us=True, city=None)

    def test_neighbourhood_resolves_through_borough(self):
        self.check("Long Island City, Queens, NY", city="Long Island City",
                   state="NY", metro="nyc")
        self.check("Long Island, New York", state="NY", us=True)

    def test_remote(self):
        self.check("Remote in USA", remote=True, us=True)
        self.check("Remote in UK", remote=True, us=False)

    def test_foreign(self):
        self.check("London, UK", us=False, country="UK")
        self.check("Toronto, ON, Canada", us=False, state="ON", country="Canada")
        self.check("Dubai - United Arab Emirates", us=False, city="Dubai")
        self.check("Canada", us=False)
        self.check("Melbourn, Royston, UK", us=False)

    def test_newark_is_two_places(self):
        self.check("Newark, NJ", metro="nyc")
        self.check("Newark, CA", metro="bay_area")

    def test_unplaceable_is_unknown_not_foreign(self):
        self.check("Springfield", us=None, city="Springfield")

    def test_garbage(self):
        for raw in (None, "", "   ", 7):
            self.assertIsNone(listings.normalize_location(raw)["us"])


class Parse(unittest.TestCase):

    def test_fixture(self):
        vs = listings.parse(_raw())
        self.assertEqual(len(vs), 18)        # two malformed rows skipped
        v = vs[0]
        self.assertEqual(v["source"], "simplify")
        self.assertTrue(v["id"].startswith("simplify:"))
        self.assertEqual(v["company"], "Optiver")
        self.assertEqual(v["posted_date"][:4], "2026")

    def test_category_spelling_is_unified(self):
        cats = {v["category"] for v in listings.parse(_raw())}
        self.assertNotIn("Software Engineering", cats)
        self.assertIn("Software", cats)

    def test_flags(self):
        vs = {v["company"]: v for v in listings.parse(_raw())}
        self.assertFalse(vs["Confluent"]["active"])
        self.assertFalse(vs["Geneva Trading"]["visible"])

    def test_malformed_input_is_empty_not_an_error(self):
        for raw in (b"", b"{", b"{}", b"\xff\xfe", "null", None, 3):
            self.assertEqual(listings.parse(raw), [])

    def test_unknown_format(self):
        self.assertEqual(listings.parse(_raw(), fmt="nope"), [])


class Dedupe(unittest.TestCase):

    def setUp(self):
        self.vs = listings.parse(_raw())

    def by(self, ls, company, title):
        hits = [x for x in ls if x["company"].startswith(company) and x["title"] == title]
        self.assertEqual(len(hits), 1, (company, title))
        return hits[0]

    def test_city_rows_merge_and_keep_their_own_links(self):
        ls = listings.dedupe(self.vs)
        opt = self.by(ls, "Optiver", "Software Engineer Intern")
        self.assertEqual(len(opt["variants"]), 3)
        raws = [l["raw"] for l in opt["locations"]]
        self.assertEqual(raws, ["Chicago, IL", "Austin, TX", "London, UK"])
        # Each city keeps the apply link of the row it came from: picking
        # Chicago must not hand back the London req.
        urls = {l["raw"]: l["url"] for l in opt["locations"]}
        self.assertIn("8604760002", urls["Chicago, IL"])
        self.assertIn("8713435002", urls["London, UK"])

    def test_repeated_cities_collapse(self):
        ls = listings.dedupe(self.vs)
        sig = self.by(ls, "Susquehanna", "Quantitative Research Intern")
        self.assertEqual(len(sig["variants"]), 4)
        raws = [l["raw"] for l in sig["locations"]]
        self.assertEqual(len(raws), len(set(raws)))
        self.assertEqual(set(raws), {"Ardmore, PA", "Philadelphia, PA", "NYC", "London, UK"})

    def test_variants_keep_their_own_degree_tags(self):
        # SIG tags one Philadelphia/NYC row PhD-only and another Master's.
        # filters.py has to judge variants, not the merged union.
        ls = listings.dedupe(self.vs)
        sig = self.by(ls, "Susquehanna", "Quantitative Research Intern")
        tags = sorted(tuple(v["degrees"]) for v in sig["variants"])
        self.assertIn(("PhD",), tags)
        self.assertIn(("Master's",), tags)

    def test_exact_duplicates_merge(self):
        ls = listings.dedupe(self.vs)
        sf = self.by(ls, "Salesforce", "Software Engineer Intern")
        self.assertEqual(len(sf["variants"]), 2)
        self.assertEqual(sf["first_posted"], min(v["posted"] for v in sf["variants"]))

    def test_dead_rows_are_dropped_before_merging(self):
        ls = listings.dedupe(self.vs)
        names = {x["company"] for x in ls}
        self.assertNotIn("Confluent", names)        # inactive
        self.assertNotIn("Geneva Trading", names)   # invisible
        everything = listings.dedupe(self.vs, live_only=False)
        self.assertIn("Confluent", {x["company"] for x in everything})

    def test_company_key_is_pluggable(self):
        a = dict(self.vs[0], company="JPMorganChase", id="simplify:a")
        b = dict(self.vs[0], company="JPMorgan Chase", id="simplify:b")
        self.assertEqual(len(listings.dedupe([a, b])), 2)
        squash = lambda s: s.lower().replace(" ", "")
        self.assertEqual(len(listings.dedupe([a, b], company_key=squash)), 1)

    def test_title_punctuation_and_case(self):
        a = dict(self.vs[0], title="Software Engineer Intern – Summer", id="simplify:a")
        b = dict(self.vs[0], title="software engineer intern - summer", id="simplify:b")
        self.assertEqual(len(listings.dedupe([a, b])), 1)


class _Resp(object):
    def __init__(self, body, status=200, etag=None):
        self._b, self.status = body, status
        self.headers = {"ETag": etag} if etag else {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FetchAndCache(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def opener(self, *responses):
        it = iter(responses)

        def _open(req, timeout=None):
            self.calls.append(dict(req.header_items()))
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        return _open

    def test_fetch_then_load(self):
        out = listings.fetch("simplify", self.dir,
                             opener=self.opener(_Resp(_raw(), etag='"v1"')))
        self.assertTrue(out["changed"])
        self.assertEqual(len(listings.load_cached("simplify", self.dir)), 18)
        # 12 live rows; Optiver x3, SIG x4 and Salesforce x2 each collapse.
        self.assertEqual(len(listings.load(self.dir)), 6)

    def test_etag_is_sent_and_304_keeps_the_cache(self):
        listings.fetch("simplify", self.dir,
                       opener=self.opener(_Resp(_raw(), etag='"v1"')))
        not_modified = urllib.error.HTTPError("u", 304, "Not Modified", {}, io.BytesIO())
        out = listings.fetch("simplify", self.dir, opener=self.opener(not_modified))
        self.assertFalse(out["changed"])
        self.assertEqual(self.calls[-1].get("If-none-match"), '"v1"')
        self.assertEqual(len(listings.load_cached("simplify", self.dir)), 18)

    def test_a_bad_download_never_replaces_a_good_cache(self):
        listings.fetch("simplify", self.dir, opener=self.opener(_Resp(_raw())))
        with self.assertRaises(ValueError):
            listings.fetch("simplify", self.dir,
                           opener=self.opener(_Resp(b"<html>rate limited</html>")))
        self.assertEqual(len(listings.load_cached("simplify", self.dir)), 18)

    def test_network_errors_propagate_and_leave_cache(self):
        listings.fetch("simplify", self.dir, opener=self.opener(_Resp(_raw())))
        with self.assertRaises(urllib.error.URLError):
            listings.fetch("simplify", self.dir,
                           opener=self.opener(urllib.error.URLError("offline")))
        self.assertEqual(len(listings.load_cached("simplify", self.dir)), 18)
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith(".tmp")], [])

    def test_no_cache_is_empty(self):
        self.assertEqual(listings.load_cached("simplify", self.dir), [])
        self.assertEqual(listings.load(self.dir), [])


if __name__ == "__main__":
    unittest.main()
