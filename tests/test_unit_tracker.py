"""Unit: tracker's matching helpers (tuned; pinned as of 2026-09-30) and role extraction."""
import unittest

import tests._support  # noqa: F401
import tracker as TR


class Matching(unittest.TestCase):
    def test_company_key_and_same_company(self):
        self.assertEqual(TR.company_key("JPMorgan Chase & Co."), TR.company_key("JPMorganChase"))
        yes = [("CTC", "Chicago Trading Company (CTC)"), ("HRT", "Hudson River Trading"), ("The Garden Depot", "Garden Depot"),
               ("CTC", "Chicago Trading Company")]
        no = [("Capital One", "Arrowstreet Capital"), ("GM", "General Motors"), ("Zenlia", "AMD"), ("", "Paylinq")]
        for a, b in yes:
            self.assertTrue(TR.same_company(a, b), (a, b))
        for a, b in no:
            self.assertFalse(TR.same_company(a, b), (a, b))

    def test_role_sim(self):
        self.assertEqual(TR.role_sim("", "Software Engineer Intern"), 0.0)
        self.assertEqual(TR.role_sim("Software Engineer Intern", "Software Engineer Intern (2027) - Austin, TX"), 0.8)
        self.assertGreaterEqual(TR.role_sim("Software Engineering Intern, Backend", "Backend Software Engineer Intern"), 0.75)
        self.assertLess(TR.role_sim("Software Engineer Intern - Summer 2027", "Software Development Intern - Summer 2027"), 0.5)

    def test_clean_role_and_extract(self):
        self.assertEqual(TR.clean_role("the JR104022–College Intern - Summer 2027 (ID: 10552937)"), "College Intern - Summer 2027")
        self.assertEqual(TR.clean_role("R-255745 Grad Intern – Software Engineer ."), "Grad Intern – Software Engineer")
        self.assertEqual(TR.extract_role("Thank you for applying", "Thank you for submitting your application for the "
                                         "Software Engineer Intern - Summer 2027 role at MacPherson. We appreciate", "MacPherson"),
                         "Software Engineer Intern - Summer 2027")
        self.assertEqual(TR.extract_role("Thank you for applying to NGI", "Thank you for your interest in career "
                                         "opportunities with NGI. Your application is under review.", "NGI"), "")

    def test_sources(self):
        self.assertEqual(TR.SOURCES, ("mail", "seed", "reported"))
