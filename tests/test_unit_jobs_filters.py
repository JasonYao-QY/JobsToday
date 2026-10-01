"""jobs/filters.py: pure, no network. Each drop must come with its reason."""

import os
import unittest

from jobs import filters, listings
from tests._jobs_support import FEED_FIXTURE as FIXTURE, V, one



class Basics(unittest.TestCase):

    def test_plain_eligible(self):
        v = filters.check(one(V()))
        self.assertTrue(v["ok"], v["reasons"])
        self.assertEqual(v["reasons"], [])
        self.assertEqual([l["raw"] for l in v["locations"]], ["NYC"])

    def test_every_drop_has_code_and_text(self):
        with open(FIXTURE, "rb") as f:
            ls = listings.dedupe(listings.parse(f.read()), live_only=False)
        _, dropped = filters.check_all(ls)
        self.assertTrue(dropped)
        for d in dropped:
            self.assertFalse(d["ok"])
            self.assertTrue(d["reasons"])
            for r in d["reasons"]:
                self.assertTrue(r["code"] and r["text"].endswith("."), r)


class Terms(unittest.TestCase):

    def test_wrong_term(self):
        v = filters.check(one(V(terms=("Summer 2026",))))
        self.assertEqual(v["reasons"][0]["code"], "term")

    def test_other_seasons_are_flagged_not_dropped(self):
        v = filters.check(one(V(terms=("Spring 2027", "Summer 2027"))))
        self.assertTrue(v["ok"])
        self.assertIn("multi_term", [f["code"] for f in v["flags"]])

    def test_title_naming_spring_only_is_dropped(self):
        v = filters.check(one(V(title="Product Manager Co-op - Spring 2027")))
        self.assertEqual(v["reasons"][0]["code"], "term")

    def test_spring_or_summer_title_is_flagged(self):
        v = filters.check(one(V(title="Analytics Intern - Spring or Summer 2027")))
        self.assertTrue(v["ok"])
        self.assertIn("multi_term", [f["code"] for f in v["flags"]])

    def test_fall_without_a_year_is_not_a_term(self):
        self.assertTrue(filters.check(one(V(title="Fall Detection ML Intern")))["ok"])

    def test_coop_flag(self):
        v = filters.check(one(V(title="Software Engineering Intern/Co-op")))
        self.assertTrue(v["ok"])
        self.assertIn("coop", [f["code"] for f in v["flags"]])


class Degrees(unittest.TestCase):

    def test_phd_only_tag_dropped(self):
        v = filters.check(one(V(degrees=("PhD",))))
        self.assertEqual(v["reasons"][0]["code"], "degree")

    def test_mba_only_tag_dropped(self):
        self.assertFalse(filters.check(one(V(degrees=("MBA",))))["ok"])

    def test_bachelors_only_tag_is_soft(self):
        v = filters.check(one(V(degrees=("Bachelor's",))))
        self.assertTrue(v["ok"])
        self.assertIn("bachelors_tag", [f["code"] for f in v["flags"]])

    def test_untagged_passes_silently(self):
        v = filters.check(one(V(degrees=())))
        self.assertTrue(v["ok"])
        self.assertEqual(v["flags"], [])

    def test_title_degree_words(self):
        drop = ["Research Scientist Intern - PhD", "Design Verification Intern - BS",
                "Strategy Intern (MBA)", "Bachelor's Software Intern"]
        keep = ["Silicon Engineering Intern - BS/MS - Multiple Teams",
                "Software Engineering Intern - Behavior Test - MS",
                "Quantitative Master’s Intern - Investments",
                "Material Master Data Intern"]
        for t in drop:
            self.assertEqual(filters.check(one(V(title=t)))["reasons"][0]["code"], "degree", t)
        for t in keep:
            self.assertTrue(filters.check(one(V(title=t)))["ok"], t)

    def test_masters_signal(self):
        self.assertTrue(filters.check(one(V(title="SWE Intern - MS")))["signals"]["masters_title"])
        self.assertTrue(filters.check(one(V(title="SWE Intern - BS/MS")))["signals"]["masters_title"])
        self.assertFalse(filters.check(one(V(title="Material Master Data Intern")))
                         ["signals"]["masters_title"])

    def test_variants_judged_separately(self):
        # SIG: same title, one Philadelphia/NYC row PhD-only, one Master's.
        phd = V(company="SIG", title="Quantitative Research Intern",
                locs=("Philadelphia, PA", "NYC"), degrees=("PhD",), vid="simplify:phd")
        ms = V(company="SIG", title="Quantitative Research Intern",
               locs=("Ardmore, PA",), degrees=("Master's",), vid="simplify:ms")
        v = filters.check(one(phd, ms))
        self.assertTrue(v["ok"])
        self.assertEqual([x["id"] for x in v["variants"]], ["simplify:ms"])
        self.assertEqual([l["raw"] for l in v["locations"]], ["Ardmore, PA"])


class ClassStanding(unittest.TestCase):

    def test_undergrad_titles(self):
        for t in ("Software Undergrad Engineer Intern",
                  "Machine Learning and Artificial Intelligence Undergraduate Intern",
                  "Business Intelligence Apprentice",
                  "Early Career Software Intern",
                  "Sophomore Technology Intern"):
            self.assertEqual(filters.check(one(V(title=t)))["reasons"][0]["code"],
                             "class_standing", t)

    def test_graduate_is_not_undergraduate(self):
        self.assertTrue(filters.check(one(V(title="Multifamily Graduate Intern")))["ok"])


class Location(unittest.TestCase):

    def test_non_us_only(self):
        v = filters.check(one(V(locs=("London, UK", "Toronto, ON, Canada"))))
        self.assertEqual(v["reasons"][0]["code"], "non_us")

    def test_mixed_keeps_us_cities_only(self):
        v = filters.check(one(V(locs=("London, UK", "NYC"))))
        self.assertTrue(v["ok"])
        self.assertEqual([l["raw"] for l in v["locations"]], ["NYC"])

    def test_london_variant_never_survives(self):
        a = V(locs=("Chicago, IL",), vid="simplify:chi")
        b = V(locs=("London, UK",), vid="simplify:lon")
        v = filters.check(one(a, b))
        self.assertEqual([x["id"] for x in v["variants"]], ["simplify:chi"])

    def test_unknown_location_is_flagged_not_dropped(self):
        v = filters.check(one(V(locs=("Springfield",))))
        self.assertTrue(v["ok"])
        self.assertIn("location_unknown", [f["code"] for f in v["flags"]])

    def test_texas(self):
        v = filters.check(one(V(locs=("Austin, TX", "Chicago, IL"))))
        self.assertTrue(v["ok"])
        self.assertIn("texas", [f["code"] for f in v["flags"]])
        self.assertTrue(v["signals"]["texas"])
        self.assertFalse(v["signals"]["texas_only"])
        self.assertTrue(filters.check(one(V(locs=("Texas",))))["signals"]["texas_only"])

    def test_remote_us(self):
        self.assertTrue(filters.check(one(V(locs=("Remote in USA",))))["ok"])


class Exclusions(unittest.TestCase):

    def test_company_wide(self):
        ex = [{"company": "Atlassian", "kind": "banned"}]
        v = filters.check(one(V(company="Atlassian", title="ML Intern")), ex)
        self.assertEqual(v["reasons"][0]["code"], "banned")
        self.assertEqual(v["reasons"][0]["text"], "Company is on the do-not-apply list.")

    def test_role_scoped_applied(self):
        ex = [{"company": "Optiver", "title": "Software Engineer Intern", "kind": "applied"}]
        self.assertFalse(filters.check(one(V(company="Optiver")), ex)["ok"])
        other = one(V(company="Optiver", title="Quantitative Research Intern"))
        self.assertTrue(filters.check(other, ex)["ok"])

    def test_pending_has_its_own_reason(self):
        ex = [{"company": "Zipline", "title": "Perception Intern", "kind": "pending",
               "note": "batch 2026-09-24"}]
        v = filters.check(one(V(company="Zipline", title="Perception Intern")), ex)
        self.assertEqual(v["reasons"][0]["code"], "pending")
        self.assertIn("batch 2026-09-24", v["reasons"][0]["text"])

    def test_pluggable_matchers(self):
        # tracker.company_key / tracker.role_sim plug in here in batch.py.
        ex = [{"company": "JPMorgan Chase", "kind": "capped"}]
        lst = one(V(company="JPMorganChase"))
        self.assertTrue(filters.check(lst, ex)["ok"])
        squash = lambda s: s.lower().replace(" ", "")
        self.assertFalse(filters.check(lst, ex, company_key=squash)["ok"])
        ex = [{"company": "Acme", "title": "SWE Intern", "kind": "applied"}]
        loose = lambda a, b: True
        self.assertFalse(filters.check(one(V()), ex, role_match=loose)["ok"])

    def test_company_match_overrides_key_equality(self):
        # tracker.same_company plugs in here: mail says "IMC", the feed says "IMC Trading".
        ex = [{"company": "IMC", "title": "Software Engineer Intern", "kind": "applied"}]
        lst = one(V(company="IMC Trading"))
        self.assertTrue(filters.check(lst, ex)["ok"])
        subset = lambda a, b: set(a.lower().split()) <= set(b.lower().split())
        self.assertFalse(filters.check(lst, ex, company_match=subset)["ok"])
        self.assertTrue(filters.check(one(V(company="Optiver")), ex, company_match=subset)["ok"])

    def test_defense(self):
        for c in ("Anduril", "L3Harris Technologies", "Northrop Grumman", "The Aerospace Corporation",
                  "AeroVironment"):
            self.assertEqual(filters.check(one(V(company=c)))["reasons"][0]["code"],
                             "defense", c)

    def test_clearance_title(self):
        v = filters.check(one(V(title="Software Intern - TS/SCI Clearance Required")))
        self.assertEqual(v["reasons"][0]["code"], "clearance")

    def test_category(self):
        v = filters.check(one(V(category="Hardware")))
        self.assertEqual(v["reasons"][0]["code"], "category")
        rules = dict(filters.DEFAULT_RULES, categories=None)
        self.assertTrue(filters.check(one(V(category="Hardware")), rules=rules)["ok"])


class Fixture(unittest.TestCase):

    def test_real_rows(self):
        with open(FIXTURE, "rb") as f:
            ls = listings.dedupe(listings.parse(f.read()))
        ok, dropped = filters.check_all(ls)
        by = {(d["company"][:9], d["title"][:20]): d for d in ok + dropped}
        opt = by[("Optiver", "Software Engineer In")]
        self.assertTrue(opt["ok"])
        self.assertEqual({l["raw"] for l in opt["locations"]}, {"Chicago, IL", "Austin, TX"})
        self.assertTrue(opt["signals"]["texas"])
        sig = by[("Susquehan", "Quantitative Researc")]
        self.assertTrue(sig["ok"])
        self.assertNotIn("London, UK", [l["raw"] for l in sig["locations"]])
        tiktok = [d for d in dropped if d["company"] == "TikTok"][0]
        self.assertEqual(tiktok["reasons"][0]["code"], "degree")
        dell = [d for d in dropped if d["company"] == "Dell Technologies"][0]
        self.assertEqual(dell["reasons"][0]["code"], "class_standing")


if __name__ == "__main__":
    unittest.main()
