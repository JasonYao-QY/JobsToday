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
                 ("no date here", "2026-09-30", "")]
        for text, sent, want in cases:
            with self.subTest(text=text):
                self.assertEqual(M.extract_due(text, sent)[0], want)

    def test_a_time_written_before_its_date_is_not_dropped(self):
        """The date is found first and the time was only looked for after it, so "<time> on <date>" came back
        as a bare date. A bare date is shown as end of day, which makes every such deadline look later than
        it is -- an 11pm deadline became "tomorrow"."""
        cases = [("Your assessment expires at 11:00pm PT on Thursday, October 01, 2026", "2026-10-01",
                  "2026-10-02 02:00"),                                  # 11pm PDT is 2am the next day here
                 ("Please complete by 9:30 AM ET on Oct 6, 2026", "2026-09-29", "2026-10-06 09:30"),
                 ("Deadline: 5pm on October 16, 2026", "2026-09-20", "2026-10-16 17:00")]
        for text, sent, want in cases:
            with self.subTest(text=text):
                self.assertEqual(M.extract_due(text, sent)[0], want)

    def test_expiry_counted_in_hours(self):
        """"expires in N days" was handled and "in N hours" was not, so a last-day reminder -- exactly the
        mail that matters most -- produced no deadline at all."""
        self.assertEqual(M.extract_due("Your assessments expire in 24 hours.", "2026-09-30")[0], "2026-10-01")
        self.assertEqual(M.extract_due("This link expires in 48 hours.", "2026-09-30")[0], "2026-10-02")

    def test_the_roblox_reminder(self):
        """The real mail, as sent on 2026-10-01. Both bugs at once: the absolute time sits behind an
        "expire in 24 hours at ..." phrase no anchor matched, and it is written before its date."""
        body = ("Hi Qingyuan,\n\nThank you for your continued interest in Roblox! Quick reminder that your "
                "assessments expire in 24 hours at 11:00pm PT on Thursday, October 01, 2026.\n\n"
                "Robots (25 minutes): Incomplete\nCoding Skills (50 minutes): Incomplete\n")
        self.assertEqual(M.extract_due(body, "2026-10-01")[0], "2026-10-02 02:00")

    def test_a_display_name_that_is_just_an_address_is_not_a_company(self):
        """Some senders put the address in the display name. tidy_company strips the "@", leaving
        "recruitment akunacapital.com", which reads as a plausible company and then matches nothing."""
        hd = {"from_name": "recruitment@akunacapital.com", "from_addr": "recruitment@akunacapital.com",
              "subject": "Your HackerRank assessment"}
        self.assertEqual(M.company_from(hd, "", platform="HackerRank"), "Akunacapital")
        hd = {"from_name": "Akuna Capital Recruiting", "from_addr": "recruitment@akunacapital.com",
              "subject": "Your HackerRank assessment"}
        self.assertEqual(M.company_from(hd, "", platform="HackerRank"), "Akuna Capital")

    def test_clean_subject(self):
        self.assertEqual(M.clean_subject("Re: Fwd:  Your   application"), "Your application")
