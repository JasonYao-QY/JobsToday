"""Acceptance: the mail classifier over stored message texts (fixture text only, no IMAP).

The expected values were recorded from the classifier as tuned on 2026-09-30. They pin behavior, right or
wrong; change an expectation only on purpose, together with the rule change that justifies it."""
import json
import os
import unittest

from tests._support import FIXTURES
from tests._surface import mail


class ClassifierTest(unittest.TestCase):
    def test_fixture_messages(self):
        M = mail()
        with open(os.path.join(FIXTURES, "mail_samples.json"), encoding="utf-8") as f:
            samples = json.load(f)
        self.assertGreaterEqual(len(samples), 30)
        for i, s in enumerate(samples):
            with self.subTest(sender=s["from_name"], subject=s["subject"][:50]):
                hd = {"subject": M.clean_subject(s["subject"]), "from_name": s["from_name"],
                      "from_addr": s["from_addr"].lower(), "date": s["date"], "msgid": "<m%d@fixture>" % i,
                      "uid": str(i).encode(), "unsub": s["unsub"]}
                kind, platform = M.classify(hd, s["body"])
                exp = s["expect"]
                self.assertEqual((kind, platform), (exp["kind"], exp["platform"]))
                if kind:
                    self.assertEqual(M.company_from(hd, s["body"], platform), exp["company"])
                if "due" in exp:
                    self.assertEqual(M.extract_due(hd["subject"] + "\n" + s["body"], hd["date"])[0], exp["due"])
                if "mins" in exp:
                    self.assertEqual(M.extract_minutes(s["body"]), exp["mins"])
