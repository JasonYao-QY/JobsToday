"""Functional: the window's Check mail path end to end with IMAP faked from the fixture messages:
Api.scan -> BackgroundTask -> mail.scan_all -> inbox + tracker -> result pushed to the page."""
from textwrap import dedent

from tests._support import SliceTest

SCRIPT = dedent("""
    import email.utils, imaplib, json, os, re, threading, time, datetime as dt
    F = json.load(open(os.path.join("tests", "fixtures", "mail_samples.json"), encoding="utf-8"))

    class FakeIMAP:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            assert timeout
        def login(self, u, p):
            pass
        def select(self, box, readonly=True):
            return "OK", [b"1"]
        def logout(self):
            pass
        def uid(self, cmd, *a):
            if cmd == "SEARCH":
                return "OK", [b" ".join(str(i + 1).encode() for i in range(len(F)))]
            out = []
            for u in [int(x) for x in a[0].split(b",")]:
                f = F[u - 1]
                when = email.utils.format_datetime(dt.datetime.fromisoformat(f["date"] + "T12:00:00+00:00"))
                hdr = "From: %s <%s>\\r\\nSubject: %s\\r\\nDate: %s\\r\\nMessage-ID: <m%d@fixture>\\r\\n%s" % (
                    f["from_name"], f["from_addr"], f["subject"], when, u, "List-Unsubscribe: <mailto:u@x>\\r\\n" if f["unsub"] else "")
                body = ("Content-Type: text/plain; charset=utf-8\\r\\n\\r\\n" + f["body"]) if "BODY.PEEK[]" in a[1] else ""
                raw = (hdr + "\\r\\n" + body).encode("utf-8")
                out.append(((b"%d (UID %d BODY[] {%d}" % (u, u, len(raw))), raw))
            return "OK", out
    imaplib.IMAP4_SSL = FakeIMAP

    import mail
    mail._security = lambda args: (None, "", "")
    mail.save_config({"accounts": ["me@gmail.com"], "hosts": {}, "scan_days": 3650, "history_since": "2026-06-01",
                      "passwords": {"me@gmail.com": "x"}})
    import today_gui as G
    pushed, done = [], threading.Event()
    class Win:
        def evaluate_js(self, js):
            pushed.append(js)
            if '"done": true' in js:
                done.set()
    api = G.Api(); api._window = Win()
    first = api.scan(); again = api.scan()
    assert done.wait(30), pushed
    st = api.state()
    apps = api.apps()
    print(json.dumps({"first": first, "again": again, "last": pushed[-1][:60], "progress": sum('"progress"' in p for p in pushed),
                      "totals": apps["totals"], "scanning": st["scanning"], "inbox": len(st["inbox"])}))
""")


class WindowScan(SliceTest):
    def test_check_mail_end_to_end(self):
        r = self.json_out(SCRIPT)
        self.assertEqual(r["first"], {"started": True})
        self.assertIn(r["again"], ({"started": False, "running": True}, {"started": True}))   # second click while busy
        self.assertTrue(r["last"].startswith("window.onScan && window.onScan({"), r["last"])
        self.assertGreater(r["progress"], 1)
        self.assertFalse(r["scanning"])
        t = r["totals"]
        self.assertEqual((t["rejected"], t["offers"]), (8, 1))       # 8 rejection fixtures, 1 offer (Paylinq)
        self.assertGreaterEqual(t["applications"], 20)
        self.assertGreater(r["inbox"], 10)
