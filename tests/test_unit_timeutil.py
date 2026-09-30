"""Unit: time and date parsing/formatting (timeutil)."""
import datetime as dt
import unittest

import tests._support  # noqa: F401  (puts the app folder on sys.path)
import timeutil as tu


class ParseTime(unittest.TestCase):
    def test_forms(self):
        cases = {"5:30 PM": "17:30", "5:30pm": "17:30", "17:30": "17:30", "5pm": "17:00", "530pm": "17:30",
                 "9am": "09:00", "12am": "00:00", "12pm": "12:00", "7": "07:00", "5": "17:00", "24": "00:00",
                 "6:10 p.m.": "18:10"}
        for s, want in cases.items():
            with self.subTest(s=s):
                self.assertEqual(tu.parse_time(s), want)

    def test_rejects(self):
        for s in ("", "lunch", "25", "5:75", None):
            with self.subTest(s=s):
                self.assertIsNone(tu.parse_time(s))

    def test_split_time_prefix(self):
        self.assertEqual(tu.split_time_prefix(["6:10pm", "Norvia", "OA"]), ("18:10", ["Norvia", "OA"]))
        self.assertEqual(tu.split_time_prefix(["5", "pm", "Gym"]), ("17:00", ["Gym"]))
        self.assertEqual(tu.split_time_prefix(["20", "jobs"]), ("", ["20", "jobs"]))


class ParseWhen(unittest.TestCase):
    def test_forms(self):
        today = dt.date.today()
        tomorrow = (today + dt.timedelta(days=1)).isoformat()
        self.assertEqual(tu.parse_when("2026-10-01 11:27"), "2026-10-01 11:27")
        self.assertEqual(tu.parse_when("2026-10-16"), "2026-10-16")
        self.assertEqual(tu.parse_when("tomorrow 9pm"), tomorrow + " 21:00")
        self.assertEqual(tu.parse_when("today"), today.isoformat())
        got = tu.parse_when("Oct 1 11:27am")
        self.assertTrue(got.endswith("-10-01 11:27"), got)
        self.assertTrue(tu.parse_when("October 16th").endswith("-10-16"))

    def test_rejects(self):
        self.assertIsNone(tu.parse_when("whenever"))
        self.assertIsNone(tu.parse_when(""))
        self.assertIsNone(tu.parse_when("tomorrow lunch"))


class Formatting(unittest.TestCase):
    def test_fmt_time_and_add_minutes(self):
        self.assertEqual(tu.fmt_time("18:10"), "6:10 PM")
        self.assertEqual(tu.fmt_time("00:05"), "12:05 AM")
        self.assertEqual(tu.fmt_time(""), "")
        self.assertEqual(tu.add_minutes("23:50", 20), "00:10")

    def test_fmt_minutes(self):
        self.assertEqual([tu.fmt_minutes(n) for n in (0, 45, 60, 75, 90, 95, 120)], ["", "45m", "1h", "75m", "90m", "1h 35m", "2h"])

    def test_until(self):
        self.assertEqual(tu.until(""), ("rolling", "none"))
        text, sev = tu.until("2000-01-01")
        self.assertEqual(sev, "over")
        self.assertTrue(text.startswith("passed "))
        far = (dt.date.today() + dt.timedelta(days=10)).isoformat()
        text, sev = tu.until(far)
        self.assertEqual((text[:3], sev), ("in ", "ok"))

    def test_dates(self):
        self.assertEqual(tu.human_date("2026-10-10"), "Saturday, October 10")
        self.assertEqual(tu.short_date("2026-10-10"), "Oct 10")
        self.assertEqual(tu.fmt_when(dt.date.today().isoformat() + " 09:00"), "today 9:00 AM")
