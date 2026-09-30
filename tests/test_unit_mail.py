"""Unit: mail helpers that encode tuned behavior. Values pin what the code does on 2026-09-30, including
gaps (e.g. "105 min HackerRank" is not read as a duration); change them only on purpose."""
import os
import tempfile
import unittest

os.environ.setdefault("TODAY_HOME", tempfile.mkdtemp(prefix="today-unit-"))
import tests._support  # noqa: E402,F401
from tests._surface import mail  # noqa: E402

M = mail()


class Mail(unittest.TestCase):
    def test_conditional_phrases_do_not_count(self):
        self.assertEqual(M.phrase_hits(M.REJECT_STRONG, "if you're not selected we will keep your resume"), 0)
        self.assertEqual(M.phrase_hits(M.REJECT_STRONG, "you were not selected for this role"), 2)
        hd = {"subject": "Application update", "from_name": "Acme", "from_addr": "jobs@acme.com", "date": "2026-09-30", "unsub": False}
        body = "Thanks for applying to the Software Engineer Intern position. If you are not selected, we will keep your resume on file."
        self.assertEqual(M.classify(hd, body), ("applied", ""))

    def test_extract_minutes(self):
        cases = {"This assessment takes 60 minutes": 60, "a 90-minute test": 90, "takes 2 hours to complete": 120,
                 "about 1.5 hours": None, "105 min HackerRank": None, "no time": None}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(M.extract_minutes(text), want)

    def test_extract_due(self):
        cases = [("Please complete within 7 days.", "2026-09-25", "2026-10-02"),
                 ("complete the assessment by Oct 6, 2026 12:46 PM ET", "2026-09-29", "2026-10-06 12:46"),
                 ("Your test must be completed by Sep 29, 2026 2:59 AM EDT", "2026-09-24", "2026-09-29 02:59"),
                 ("Deadline: October 16", "2026-09-20", "2026-10-16"),
                 ("expires in 24 hours", "2026-09-30", ""), ("no date here", "2026-09-30", "")]
        for text, sent, want in cases:
            with self.subTest(text=text):
                self.assertEqual(M.extract_due(text, sent)[0], want)

    def test_clean_subject(self):
        self.assertEqual(M.clean_subject("Re: Fwd:  Your   application"), "Your application")
