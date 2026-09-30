"""Smoke: the window's bridge (today_gui.Api) still imports and answers against a temp data folder.

The Api swallows exceptions into {"error": ...} to keep the window alive, so this checks for that key
explicitly. Behavior is covered by the CLI acceptance tests; this only guards the wiring the window uses."""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests._support import ROOT

SCRIPT = textwrap.dedent("""
    import json, today_gui as G
    a = G.Api()
    st = a.state()
    assert "error" not in st, st
    r = a.plan_add(st["date"], "6:10pm Norvia OA 75m"); assert "error" not in r, r
    st = a.state(); assert st["items"][0]["time_fmt"] == "6:10 PM", st["items"]
    for call in (lambda: a.daily_add("Apply for 20 jobs"), lambda: a.deadline_add("Exam", "tomorrow 9am"),
                 lambda: a.apps(), lambda: a.version(), lambda: a.accounts()):
        r = call(); assert not (isinstance(r, dict) and "error" in r), r
    print("ok")
""")


class WindowBridgeSmoke(unittest.TestCase):
    def test_api_answers(self):
        home = tempfile.mkdtemp(prefix="today-gui-")
        self.addCleanup(shutil.rmtree, home, True)
        env = dict(os.environ, TODAY_HOME=home, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=ROOT)
        p = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ok", p.stdout)
