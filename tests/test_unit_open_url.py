"""Unit: which browser a link from the window opens in.

`open <url>` hands the link to the system default; `open -a <app> <url>` names one. The argv is built by a
pure function so this never has to launch a browser.
"""
import os
import tempfile
import unittest

os.environ.setdefault("TODAY_HOME", tempfile.mkdtemp(prefix="today-unit-"))
import tests._support  # noqa: E402,F401
import today_gui as G  # noqa: E402

URL = "https://example.com/a"


class OpenCmd(unittest.TestCase):
    def test_no_setting_uses_the_system_default(self):
        self.assertEqual(G.open_cmd(URL, "", "darwin"), ["open", URL])

    def test_a_named_browser_is_asked_for_by_name(self):
        self.assertEqual(G.open_cmd(URL, "Google Chrome", "darwin"), ["open", "-a", "Google Chrome", URL])

    def test_blank_and_padded_settings(self):
        self.assertEqual(G.open_cmd(URL, "   ", "darwin"), ["open", URL])
        self.assertEqual(G.open_cmd(URL, " Firefox ", "darwin"), ["open", "-a", "Firefox", URL])

    def test_elsewhere_the_setting_is_ignored(self):
        """-a is an `open` flag and xdg-open has no equivalent, so naming a browser must not break links."""
        self.assertEqual(G.open_cmd(URL, "Google Chrome", "linux"), ["xdg-open", URL])


class Setting(unittest.TestCase):
    def test_browser_reads_the_env_file_like_the_other_settings(self):
        import env
        old = os.environ.pop("TODAY_BROWSER", None)
        try:
            os.environ["TODAY_BROWSER"] = "Google Chrome"
            self.assertEqual(env.browser(), "Google Chrome")
            del os.environ["TODAY_BROWSER"]
            self.assertEqual(env.browser(), "")
        finally:
            if old is not None:
                os.environ["TODAY_BROWSER"] = old
