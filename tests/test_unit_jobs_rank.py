"""jobs/rank.py: pure. The order follows his stated preferences, and every
point is explained."""

import unittest

from jobs import filters, listings, rank
from tests._jobs_support import V, one

NOW = 1790900000


def E(**kw):
    """Score one listing built like test_jobs_filters.V."""
    v = filters.check(one(V(**kw)))
    assert v["ok"], v["reasons"]
    return rank.score(v, now=NOW)


class Explanation(unittest.TestCase):

    def test_score_is_the_sum_of_its_parts(self):
        e = E(company="DoorDash", title="Machine Learning Engineer Intern - Masters",
              locs=("Seattle, WA",))
        self.assertEqual(e["score"], sum(p["points"] for p in e["parts"]))
        texts = " ".join(p["text"] for p in e["parts"])
        self.assertIn("model-building", texts)
        self.assertIn("Large firm", texts)
        self.assertIn("Seattle", texts)
        self.assertIn("Master's", texts)
        for p in e["parts"]:
            self.assertTrue(p["text"].endswith("."), p)


class WorkType(unittest.TestCase):

    def test_model_building_above_evaluation(self):
        build = E(title="Machine Learning Engineer Intern")
        evalu = E(title="ML Evaluation Intern")
        self.assertGreater(build["score"], evalu["score"])

    def test_model_risk_validation_ranks_low_even_at_a_good_firm(self):
        risk = E(company="Goldman Sachs", title="Model Risk Validation Intern")
        small_swe = E(company="Tiny Co", title="Software Engineer Intern",
                      locs=("Des Moines, IA",))
        self.assertLess(risk["score"], small_swe["score"])
        self.assertLessEqual(risk["score"], 0)      # -6 work, +3 firm, +3 NYC

    def test_labeling_is_evaluation_work(self):
        e = E(company="Waymo", title="Software Engineering Intern - Labeling")
        self.assertIn("evaluation", e["parts"][0]["text"])

    def test_master_data_is_not_model_building(self):
        e = E(title="Material Master Data Intern")
        self.assertNotIn("model-building", e["parts"][0]["text"])


class FirmAndCity(unittest.TestCase):

    def test_large_firm_above_small_robotics_startup(self):
        big = E(company="Salesforce", title="Software Engineer Intern", locs=("SF",))
        bot = E(company="Acme Robotics", title="Software Engineer Intern", locs=("SF",))
        self.assertGreater(big["score"], bot["score"])
        self.assertIn("robotics", " ".join(p["text"] for p in bot["parts"]))

    def test_swe_prefers_bay_area_and_seattle(self):
        a = E(title="Software Engineer Intern", locs=("Austin, TX", "Seattle, WA", "NYC"))
        self.assertEqual(a["city"], "Seattle, WA")
        self.assertEqual(a["track"], "swe")

    def test_finance_prefers_nyc_chicago_boston(self):
        a = E(company="Optiver", title="Software Engineer Intern",
              locs=("SF", "Chicago, IL"))
        self.assertEqual(a["city"], "Chicago, IL")
        self.assertEqual(a["track"], "finance")

    def test_chosen_city_brings_its_own_link(self):
        chi = V(company="Optiver", locs=("Chicago, IL",), vid="simplify:chi")
        aus = V(company="Optiver", locs=("Austin, TX",), vid="simplify:aus")
        e = rank.score(filters.check(one(aus, chi)), now=NOW)
        self.assertEqual(e["city"], "Chicago, IL")
        self.assertEqual(e["variant_id"], "simplify:chi")
        self.assertTrue(e["url"].endswith("simplify:chi"))

    def test_quant_category_is_finance(self):
        e = E(company="Unknown LLC", title="Quantitative Developer Intern", category="Quant")
        self.assertEqual(e["track"], "finance")


class Adjustments(unittest.TestCase):

    def test_flags_cost_a_point_and_say_why(self):
        tx = E(title="Software Engineer Intern", locs=("Austin, TX",))
        ny = E(title="Software Engineer Intern", locs=("Denver, CO",))
        self.assertEqual(tx["score"], ny["score"] - 1)
        self.assertIn("Texas", " ".join(p["text"] for p in tx["parts"]))

    def test_bachelors_tag(self):
        b = E(degrees=("Bachelor's",))
        m = E(degrees=("Bachelor's", "Master's"))
        self.assertEqual(b["score"], m["score"] - 1)

    def test_fresh(self):
        old = rank.score(filters.check(one(V())), now=NOW)          # posted=1
        v = V()
        v["posted"] = NOW - 3600
        new = rank.score(filters.check(one(v)), now=NOW)
        self.assertEqual(new["score"] - old["score"], 0.5)


class Resume(unittest.TestCase):

    def check(self, company, title, want, category="Software"):
        v = filters.check(one(V(company=company, title=title, category=category)))
        self.assertEqual(rank.resume_for(v)[0], want, (company, title))

    def test_variants(self):
        self.check("DRW", "Software Developer Intern", "SWE-fin")
        self.check("Stripe", "Backend Engineer Intern", "SWE-fin")
        self.check("Optiver", "Software Engineer Intern", "SWE-fin")
        self.check("BlackRock", "Quantitative Master's Intern - Investments", "Data", "Quant")
        self.check("Two Sigma", "Quantitative Researcher Intern", "Data", "Quant")
        self.check("Figma", "Software Engineer Intern", "SWE")
        self.check("DoorDash", "Machine Learning Engineer Intern", "AIML")
        self.check("Capital One", "AI Engineer Intern", "AIML")
        self.check("Phonic", "LLM Research Intern", "MLLM")
        self.check("Bedrock", "Multimodal Evaluation Intern", "MLLM")
        self.check("Red Ventures", "Data Science Intern", "Data", "AI/ML/Data")


class Order(unittest.TestCase):

    def test_stable_and_best_first(self):
        vs = [V(company=c, title=t, locs=l, vid="simplify:%d" % i) for i, (c, t, l) in enumerate([
            ("Tiny Co", "Marketing Analytics Intern", ("Rosemont, IL",)),
            ("DoorDash", "Machine Learning Engineer Intern", ("Seattle, WA",)),
            ("Assurant", "Software Engineer Intern", ("Miami, FL",)),
        ])]
        verdicts = [filters.check(x) for x in listings.dedupe(vs)]
        a = [e["company"] for e in rank.rank(verdicts, now=NOW)]
        b = [e["company"] for e in rank.rank(list(reversed(verdicts)), now=NOW)]
        self.assertEqual(a, b)
        self.assertEqual(a[0], "DoorDash")
        self.assertEqual(a[-1], "Tiny Co")


if __name__ == "__main__":
    unittest.main()
