"""Functional: adding and removing a mail account through mail's public functions, with IMAP faked.

Runs where the macOS `security` tool is absent, so passwords fall back to config.json (mode 600); the
Keychain path is exercised only on the Mac."""
from textwrap import dedent

from tests._support import SliceTest

FAKE = dedent("""
    import imaplib, json, os
    class Fake:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            assert timeout, "IMAP connections must have a timeout"
        def login(self, user, pw):
            if pw != "abcdabcdabcdabcd":
                raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED]   Invalid credentials (Failure)")
        def logout(self):
            pass
    imaplib.IMAP4_SSL = Fake
    import mail
    mail._security = lambda args: (None, "", "")
""")


class MailAccounts(SliceTest):
    def test_add_list_remove(self):
        out = self.json_out(FAKE + dedent("""
            where = mail.add_account("  Me@Gmail.com ", "abcd abcd abcd abcd")
            cfg = mail.load_config()
            mode = oct(os.stat(mail.CONFIG_FILE).st_mode & 0o777)
            state = [where, cfg["accounts"], mail.has_password("me@gmail.com"), mode]
            mail.remove_account("me@gmail.com")
            state += [mail.load_config()["accounts"], mail.has_password("me@gmail.com")]
            print(json.dumps(state))
        """))
        self.assertEqual(out, ["config", ["me@gmail.com"], True, "0o600", [], False])

    def test_refused_login_saves_nothing(self):
        out = self.json_out(FAKE + dedent("""
            errs = []
            for email, pw in (("me@gmail.com", "wrong"), ("not-an-email", "x"), ("me@gmail.com", "   ")):
                try:
                    mail.add_account(email, pw)
                except (mail.LoginFailed, ValueError) as e:
                    errs.append([type(e).__name__, str(e)])
            print(json.dumps([errs, mail.load_config()["accounts"], os.path.exists(mail.CONFIG_FILE)]))
        """))
        errs, accounts, cfg_written = out
        self.assertEqual(errs[0], ["LoginFailed", "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"])  # whitespace collapsed
        self.assertEqual([e[0] for e in errs[1:]], ["ValueError", "ValueError"])
        self.assertEqual((accounts, cfg_written), ([], False))
