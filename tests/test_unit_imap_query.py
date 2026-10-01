"""Unit: the Gmail search the scan issues.

This exists because the extension name is a magic string that a careless find-and-replace once turned
into X-AV-RAW. Gmail answers BAD, imap_search swallows it and falls back to a plain SINCE search, and the
scan then reads every recent message instead of the matching ones — slower, and much worse input to the
classifier. Nothing visibly breaks, which is why it needs a test.
"""
import unittest

import tests._support  # noqa: F401
import mail


class FakeIMAP:
    def __init__(self, raw_ok=True):
        self.calls = []
        self.raw_ok = raw_ok

    def uid(self, *args):
        self.calls.append(args)
        if args[1] == "X-GM-RAW":
            return ("OK", [b"11 12"]) if self.raw_ok else ("BAD", [None])
        return ("OK", [b"90 91 92"])


class Query(unittest.TestCase):
    def test_uses_the_gmail_extension(self):
        M = FakeIMAP()
        self.assertEqual(mail.imap_search(M, 30), [b"11", b"12"])
        self.assertEqual(M.calls[0][1], "X-GM-RAW")

    def test_asks_for_calendar_attachments_as_well_as_job_wording(self):
        M = FakeIMAP()
        mail.imap_search(M, 30)
        q = M.calls[0][2]
        self.assertIn("filename:ics", q)
        self.assertIn("interview", q)
        self.assertIn("newer_than:30d", q)

    def test_history_window_uses_after(self):
        M = FakeIMAP()
        mail.imap_search(M, 30, since="2026-06-14")
        self.assertIn("after:2026/06/14", M.calls[0][2])

    def test_falls_back_to_a_plain_search_when_the_extension_is_refused(self):
        M = FakeIMAP(raw_ok=False)
        self.assertEqual(mail.imap_search(M, 30), [b"90", b"91", b"92"])
        self.assertEqual(M.calls[-1][1], "SINCE")


if __name__ == "__main__":
    unittest.main()
