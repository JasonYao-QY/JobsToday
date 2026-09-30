"""Unit: the window's page is web/index.html with app.css and app.js inlined (loaded from memory, no server)."""
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("TODAY_HOME", tempfile.mkdtemp(prefix="today-unit-"))
import tests._support  # noqa: E402,F401
import today_gui as G  # noqa: E402


class Page(unittest.TestCase):
    def test_inlined_one_page(self):
        html = G.load_page()
        self.assertNotIn('href="app.css"', html)
        self.assertNotIn('src="app.js"', html)
        self.assertEqual((html.count("<style>"), html.count("<script>")), (1, 1))
        self.assertIn("window.pywebview.api", html)
        for host in ("localhost", "127.0.0.1", "http://"):
            self.assertNotIn(host, html)

    def test_broken_split_is_refused(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("index.html", "app.css", "app.js"):
            shutil.copy(os.path.join(G.WEB_DIR, name), d)
        with open(os.path.join(d, "app.js"), "a") as f:
            f.write("\n// </script>\n")
        old, G.WEB_DIR = G.WEB_DIR, d
        try:
            with self.assertRaises(ValueError):
                G.load_page()
        finally:
            G.WEB_DIR = old
