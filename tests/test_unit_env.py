"""Unit: env (local settings from the real environment, else .env in the data folder, else a default)."""
import os
import unittest

import tests._support  # noqa: F401
from tests._support import run_py
import env


class Parse(unittest.TestCase):
    def test_forms(self):
        got = env._parse(
            "# comment\n"
            "\n"
            "PLAIN=one\n"
            "export EXPORTED=two\n"
            '  QUOTED = "three"  \n'
            "SINGLE='four'\n"
            "EMPTY=\n"
            "no_equals_sign\n"
            "=novalue\n"
        )
        self.assertEqual(got, {"PLAIN": "one", "EXPORTED": "two", "QUOTED": "three",
                               "SINGLE": "four", "EMPTY": ""})


class Get(unittest.TestCase):
    def test_missing_setting_falls_back(self):
        self.assertEqual(env.get("TODAY_NO_SUCH_SETTING", "fallback"), "fallback")

    def test_real_environment_wins(self):
        os.environ["TODAY_TEST_SETTING"] = "from-environ"
        self.addCleanup(os.environ.pop, "TODAY_TEST_SETTING", None)
        self.assertEqual(env.get("TODAY_TEST_SETTING"), "from-environ")

    def test_account_hints_splits_and_trims(self):
        os.environ["TODAY_ACCOUNT_HINTS"] = " a@x.com , b@y.edu ;; "
        self.addCleanup(os.environ.pop, "TODAY_ACCOUNT_HINTS", None)
        self.assertEqual(env.account_hints(), ["a@x.com", "b@y.edu"])

    def test_nothing_configured_is_no_hints(self):
        os.environ["TODAY_ACCOUNT_HINTS"] = ""
        self.addCleanup(os.environ.pop, "TODAY_ACCOUNT_HINTS", None)
        self.assertEqual(env.account_hints(), [])


class NameTokens(unittest.TestCase):
    def test_lowercased_and_split(self):
        os.environ["TODAY_NAME_TOKENS"] = "Ada, Lovelace"
        self.addCleanup(os.environ.pop, "TODAY_NAME_TOKENS", None)
        self.assertEqual(env.name_tokens(), ["ada", "lovelace"])

    def test_role_extraction_stops_at_a_configured_name(self):
        """With the tokens set, the trailing name is not read as part of the role; without them the
        subject is left alone (so a clone with no .env behaves predictably)."""
        subject = "TRN Application Status - Data Scientist Intern - Ada Lovelace"
        code = """
            import os
            os.environ["TODAY_NAME_TOKENS"] = "ada,lovelace"
            import tracker
            print(repr(tracker.extract_role({subject!r}, "", "TRN")))
        """.format(subject=subject)
        out = run_py(self, os.environ["TODAY_HOME"], code)
        self.assertIn("Data Scientist Intern", out)
        self.assertNotIn("Lovelace", out)


class File(unittest.TestCase):
    def test_reads_dot_env_from_the_data_folder(self):
        out = run_py(self, os.environ["TODAY_HOME"], """
            import os
            from store import APP_DIR
            with open(os.path.join(APP_DIR, ".env"), "w") as f:
                f.write("# hints\\nTODAY_ACCOUNT_HINTS=one@example.com,two@example.edu\\n")
            import env
            print(env.account_hints())
        """)
        self.assertIn("one@example.com", out)
        self.assertIn("two@example.edu", out)


if __name__ == "__main__":
    unittest.main()
