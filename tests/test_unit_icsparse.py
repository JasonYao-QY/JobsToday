"""Unit: icsparse (the text/calendar part of a message -> event dicts)."""
import unittest

import tests._support  # noqa: F401
import icsparse


def ics(*lines):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "\r\n".join(lines) + "\r\nEND:VCALENDAR\r\n"


EVENT = ics(
    "METHOD:REQUEST",
    "BEGIN:VEVENT",
    "UID:abc123@google.com",
    "DTSTART;TZID=America/New_York:20261005T150000",
    "DTEND;TZID=America/New_York:20261005T160000",
    "SUMMARY:Mock interview with the career center",
    "LOCATION:Tepper 2401",
    "ORGANIZER;CN=Career Services:mailto:careers@example.edu",
    "END:VEVENT",
)


class Parse(unittest.TestCase):
    def test_full_invitation(self):
        e, = icsparse.parse(EVENT)
        self.assertEqual(e["uid"], "abc123@google.com")
        self.assertEqual(e["summary"], "Mock interview with the career center")
        self.assertEqual(e["location"], "Tepper 2401")
        self.assertEqual(e["organizer"], "Career Services")
        self.assertEqual(e["method"], "REQUEST")
        self.assertFalse(e["cancelled"])
        self.assertFalse(e["all_day"])
        self.assertTrue(e["start"].startswith("2026-10-05 "))   # local time; the zone decides the clock

    def test_tzid_converts_to_local(self):
        """15:00 New York is 19:00 UTC; the suite pins TZ to New York, so it stays 15:00."""
        e, = icsparse.parse(EVENT)
        self.assertEqual(e["start"], "2026-10-05 15:00")
        self.assertEqual(e["end"], "2026-10-05 16:00")

    def test_utc_stamp_converts(self):
        e, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART:20261005T190000Z", "SUMMARY:UTC", "END:VEVENT"))
        self.assertEqual(e["start"], "2026-10-05 15:00")

    def test_floating_time_is_taken_as_local(self):
        e, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART:20261005T150000", "SUMMARY:Floating", "END:VEVENT"))
        self.assertEqual(e["start"], "2026-10-05 15:00")

    def test_all_day(self):
        e, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261005", "SUMMARY:Career fair", "END:VEVENT"))
        self.assertEqual((e["start"], e["all_day"]), ("2026-10-05", True))

    def test_folded_lines_and_escapes(self):
        e, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART:20261005T150000",
                                "SUMMARY:Coffee with Prof. Smith\r\n  about 17-695",
                                "LOCATION:Gates 4th floor\\, west", "END:VEVENT"))
        self.assertEqual(e["summary"], "Coffee with Prof. Smith about 17-695")
        self.assertEqual(e["location"], "Gates 4th floor, west")

    def test_cancellation_is_flagged_both_ways(self):
        by_method, = icsparse.parse(ics("METHOD:CANCEL", "BEGIN:VEVENT", "DTSTART:20261005T150000",
                                        "SUMMARY:Off", "END:VEVENT"))
        by_status, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART:20261005T150000", "STATUS:CANCELLED",
                                        "SUMMARY:Off", "END:VEVENT"))
        self.assertTrue(by_method["cancelled"])
        self.assertTrue(by_status["cancelled"])

    def test_several_events(self):
        two = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART:20261005T150000", "SUMMARY:One", "END:VEVENT",
                                 "BEGIN:VEVENT", "DTSTART:20261006T090000", "SUMMARY:Two", "END:VEVENT"))
        self.assertEqual([e["summary"] for e in two], ["One", "Two"])

    def test_junk_is_not_an_exception(self):
        for bad in ("", "not a calendar", ics("BEGIN:VEVENT", "SUMMARY:No start", "END:VEVENT"),
                    ics("BEGIN:VEVENT", "DTSTART:garbage", "SUMMARY:Bad", "END:VEVENT"),
                    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261005T150000\r\nSUMMARY:Truncated"):
            self.assertEqual(icsparse.parse(bad), [], bad[:40])

    def test_unknown_tzid_falls_back_to_local(self):
        e, = icsparse.parse(ics("BEGIN:VEVENT", "DTSTART;TZID=Mars/Olympus:20261005T150000",
                                "SUMMARY:Elsewhere", "END:VEVENT"))
        self.assertEqual(e["start"], "2026-10-05 15:00")


if __name__ == "__main__":
    unittest.main()
