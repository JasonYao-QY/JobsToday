"""Shared helpers for the tests.

The window is the only frontend, so tests reach the app two ways: a slice's public functions against real
JSON in a throwaway TODAY_HOME (SliceTest), or the Api the page calls (see test_smoke_window_bridge and
test_functional_window_scan). Both check behavior anyone can observe and keep passing however the code is
arranged inside.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run_py(testcase, home, code):
    """Run a snippet in a fresh interpreter with TODAY_HOME=home (modules read it at import time)."""
    import textwrap
    env = dict(os.environ, TODAY_HOME=home, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, env=env,
                       cwd=ROOT, timeout=60)
    testcase.assertEqual(p.returncode, 0, p.stdout + p.stderr)
    return p.stdout


class SliceTest(unittest.TestCase):
    """A slice's public functions against real JSON in a temp folder."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="today-slice-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def py(self, code):
        return run_py(self, self.home, code)

    def json_out(self, code):
        return json.loads(self.py(code).strip().splitlines()[-1])
