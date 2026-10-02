"""Unit: jobs/letter.py -- the house-style check and the prompt, with no network.

`problems()` is the part worth pinning: it is what makes the writer rewrite, and it encodes the rules he
keeps repeating (no dashes standing in for clauses, no stock openers, no three-item rhythm).
"""
import os
import tempfile
import unittest

os.environ.setdefault("TODAY_HOME", tempfile.mkdtemp(prefix="today-unit-"))
import tests._support  # noqa: E402,F401
from jobs import letter  # noqa: E402

GOOD = (
    "Dear Hiring Team,\n\n"
    "I am applying for the Software Engineer Intern role at Norvia. At RoboScience I rebuilt a 6D-pose "
    "quality pipeline and tuned its metrics to an F1 of 0.982 over a 128,000 segment corpus. The part I "
    "found most useful was tracking down a rotation initialisation bug that nobody had isolated; the fix "
    "moved tracking from 0.033 to 0.356.\n\n"
    "Before that I spent a summer at JD.com building ETL over two million records and a credit scoring "
    "model that lifted AUC by twelve percent. Your posting asks for someone comfortable moving between "
    "data work and service code, which is most of what those two jobs were.\n\n"
    "The posting mentions reconciling data that arrives from several systems at different times. That was "
    "the awkward part of the SUNRATE work: I rebuilt a cross border payments intranet where the same "
    "transaction showed up from three sources, and most of the effort went into deciding which one to "
    "trust rather than into the interface on top. I expect your version of that problem is larger, but it "
    "is the kind of thing I like being handed.\n\n"
    "I am in the first year of the Master of Software Engineering at Carnegie Mellon and finish in "
    "December 2027, so I am available for the full summer. I would be glad to talk.\n\n"
    "Qingyuan Yao\n")


class Problems(unittest.TestCase):
    def test_a_clean_draft_has_none(self):
        self.assertEqual(letter.problems(GOOD), [])

    def test_dashes_standing_in_for_a_clause(self):
        for bad in ["I built the pipeline — it worked well — and shipped it.",
                    "I built the pipeline -- it worked well.",
                    "the fix – a one line change – held up"]:
            with self.subTest(bad=bad):
                self.assertIn("dash", " ".join(letter.problems(GOOD.replace("I would be glad to talk.", bad))))

    def test_stock_openers_and_filler(self):
        for bad in ["I am excited to apply for this role.",
                    "I am writing to express my interest in the position.",
                    "I am deeply passionate about distributed systems.",
                    "My background aligns perfectly with your needs."]:
            with self.subTest(bad=bad):
                self.assertTrue(any("stock" in p for p in letter.problems(GOOD + bad)))

    def test_three_item_parallel_rhythm(self):
        triads = (" I have worked on data pipelines, model training, and evaluation harnesses."
                  " I care about correctness, clarity, and speed."
                  " The work spanned research, engineering, and operations.")
        self.assertTrue(any("parallel" in p for p in letter.problems(GOOD + triads)))

    def test_one_list_is_allowed(self):
        """A single enumeration is ordinary writing. It is the repeated rhythm that reads as generated."""
        self.assertEqual(letter.problems(GOOD + " I used Python, Go, and SQL there."), [])

    def test_length(self):
        self.assertTrue(any("short" in p for p in letter.problems("Dear Hiring Team, I would like the job.")))
        self.assertTrue(any("long" in p for p in letter.problems(GOOD + (" word" * 500))))


class Prompting(unittest.TestCase):
    def setUp(self):
        self.profile = {"name": "Qingyuan Yao", "shared": ["CMU MSE, Dec 2027"],
                        "variants": {"SWE": ["RoboScience: F1 0.982"], "Data": ["JD.com: AUC +12%"]}}
        self.entry = {"company": "Norvia", "title": "Software Engineer Intern", "city": "NYC",
                      "url": "https://example.com/j"}

    def test_only_the_chosen_variant_reaches_the_prompt(self):
        """A letter may not claim anything the resume he is actually sending does not say."""
        w = letter.Writer("k", transport=lambda p: {"content": [{"type": "text", "text": GOOD}]})
        seen = {}
        w._transport = lambda p: seen.update(p) or {"content": [{"type": "text", "text": GOOD}]}
        w.write(self.entry, "posting text", self.profile, "SWE")
        user = seen["messages"][0]["content"]
        self.assertIn("RoboScience: F1 0.982", user)
        self.assertNotIn("AUC +12%", user)
        self.assertIn("CMU MSE, Dec 2027", user)
        self.assertIn("Norvia", user)

    def test_an_unclean_draft_is_sent_back_once(self):
        drafts = ["Dear Team, I am excited to apply — truly. " + ("word " * 200), GOOD]
        calls = []

        def transport(payload):
            calls.append(payload)
            return {"content": [{"type": "text", "text": drafts[len(calls) - 1]}]}

        out = letter.Writer("k", transport=transport).write(self.entry, "posting", self.profile, "SWE")
        self.assertEqual(len(calls), 2)
        self.assertTrue(out["retried"])
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["text"], GOOD.strip())
        # the second attempt is told what was wrong with the first
        self.assertIn("dash", json_of(calls[1]))

    def test_a_draft_that_stays_unclean_is_returned_with_its_problems(self):
        bad = "Dear Team, I am excited to apply. " + ("word " * 200)
        out = letter.Writer("k", transport=lambda p: {"content": [{"type": "text", "text": bad}]}).write(
            self.entry, "posting", self.profile, "SWE")
        self.assertTrue(out["problems"])
        self.assertTrue(out["text"])            # still handed over; he can edit it

    def test_the_key_never_leaves(self):
        w = letter.Writer("sk-ant-secret", transport=lambda p: {"content": [{"type": "text", "text": GOOD}]})
        out = w.write(self.entry, "posting", self.profile, "SWE")
        self.assertNotIn("sk-ant-secret", repr(w) + json_of(out))

    def test_a_transport_error_is_reported_not_raised(self):
        def boom(payload):
            raise OSError("no network")
        out = letter.Writer("k", transport=boom).write(self.entry, "posting", self.profile, "SWE")
        self.assertIn("no network", out["error"])
        self.assertEqual(out["text"], "")


def json_of(x):
    import json
    return json.dumps(x)


class ApiErrors(unittest.TestCase):
    """An HTTP error from the API carries the reason in its body. Reporting only "HTTP Error 400: Bad
    Request" throws away the one thing that says what to do about it."""

    def _http_error(self, code, body):
        import io
        import urllib.error
        return urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "Bad Request",
                                      {}, io.BytesIO(body.encode("utf-8")))

    def test_the_api_message_reaches_the_tab(self):
        err = self._http_error(400, '{"type":"error","error":{"type":"invalid_request_error",'
                                    '"message":"Your credit balance is too low to access the API."}}')

        def boom(payload):
            raise err

        out = letter.Writer("k", transport=boom).write(
            {"company": "A", "title": "B"}, "posting",
            {"shared": ["x"], "variants": {"SWE": ["y"]}}, "SWE")
        self.assertIn("credit balance is too low", out["error"])
        self.assertIn("400", out["error"])

    def test_a_body_that_is_not_json_is_still_shown(self):
        def boom(payload):
            raise self._http_error(529, "overloaded")

        out = letter.Writer("k", transport=boom).write(
            {"company": "A", "title": "B"}, "posting",
            {"shared": ["x"], "variants": {"SWE": ["y"]}}, "SWE")
        self.assertIn("529", out["error"])
        self.assertIn("overloaded", out["error"])
