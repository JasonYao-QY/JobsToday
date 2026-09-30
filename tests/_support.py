"""Shared helpers for the tests.

Acceptance tests drive the real `today` command in a subprocess against a throwaway TODAY_HOME, so they
check behavior anyone can observe (output, exit codes, the JSON files the window also reads) and keep
passing however the code is arranged inside.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "today.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def short(d):
    return "%s %d" % (d.strftime("%b"), d.day)


class CliTest(unittest.TestCase):
    """Each test gets an empty data folder. Dates are computed at setUp; a test that straddles midnight may fail."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="today-test-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.today = dt.date.today()
        self.yesterday = self.today - dt.timedelta(days=1)
        self.tomorrow = self.today + dt.timedelta(days=1)

    def cli(self, *args, ok=True):
        env = dict(os.environ, TODAY_HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, ENTRY] + [str(a) for a in args], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, timeout=60)
        if ok and p.returncode != 0:
            self.fail("today %s exited %d\nstdout: %s\nstderr: %s" % (" ".join(map(str, args)), p.returncode, p.stdout, p.stderr))
        if not ok and p.returncode == 0:
            self.fail("today %s should have failed\nstdout: %s" % (" ".join(map(str, args)), p.stdout))
        return p

    def out(self, *args):
        return self.cli(*args).stdout

    def data(self, rel):
        with open(os.path.join(self.home, rel), encoding="utf-8") as f:
            return json.load(f)

    def put(self, rel, obj):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def section(self, text, name):
        """The lines of one section of `today show` (DEADLINES, SCHEDULE, DAILY, INBOX)."""
        lines, on = [], False
        for line in text.splitlines():
            if line.strip() in ("DEADLINES", "SCHEDULE", "DAILY") or line.startswith("INBOX"):
                on = line.strip() == name or (name == "INBOX" and line.startswith("INBOX"))
                continue
            if on and line.strip():
                lines.append(line)
        return lines
