"""jobs/verify.py: no network, no API calls. Transports and fetchers are fakes."""

import io
import json
import unittest
import urllib.error

from jobs import verify

LONG = ("This internship is for Summer 2027 in New York. Open to Master's students "
        "graduating between December 2026 and June 2028. ") * 8


class _Resp(object):
    def __init__(self, body, ct="text/html; charset=utf-8"):
        self._b = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = {"Content-Type": ct}

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(body, ct="text/html; charset=utf-8", seen=None):
    def _open(req, timeout=None):
        if seen is not None:
            seen.append(req.full_url)
        if isinstance(body, Exception):
            raise body
        return _Resp(body, ct)
    return _open


class PageUrl(unittest.TestCase):

    def test_workday_goes_to_the_json_endpoint(self):
        u = ("https://assurant.wd1.myworkdayjobs.com/en-US/Assurant_Careers/job/"
             "United-States-Virtual/Summer-2027-Intern_R-115659")
        got, kind = verify.page_url(u)
        self.assertEqual(kind, "workday")
        self.assertEqual(got, "https://assurant.wd1.myworkdayjobs.com/wday/cxs/assurant/"
                              "Assurant_Careers/job/United-States-Virtual/Summer-2027-Intern_R-115659")

    def test_without_locale(self):
        u = "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/SF/SWE_JR1"
        self.assertEqual(verify.page_url(u)[0],
                         "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/"
                         "External_Career_Site/job/SF/SWE_JR1")

    def test_other_hosts_untouched(self):
        u = "https://www.optiver.com/join-us/jobs/8604760002/?gh_jid=8604760002"
        self.assertEqual(verify.page_url(u), (u, "html"))


class PageText(unittest.TestCase):

    def test_html_strips_scripts_and_keeps_blocks(self):
        body = ("<html><head><title>x</title><script>var a=1;</script></head><body>"
                "<h1>SWE Intern</h1><p>%s</p><style>p{}</style></body></html>" % LONG)
        p = verify.page_text("https://x.example/job", opener=opener_for(body))
        self.assertTrue(p["readable"])
        self.assertNotIn("var a", p["text"])
        self.assertTrue(p["text"].startswith("SWE Intern\n"))

    def test_json_ld_is_used_when_the_page_is_a_shell(self):
        ld = {"@type": "JobPosting", "description": "&lt;p&gt;%s&lt;/p&gt;" % LONG,
              "baseSalary": {"value": {"minValue": 45, "unitText": "HOUR"}}}
        body = ('<html><body><div id="root"></div><script type="application/ld+json">%s'
                '</script></body></html>' % json.dumps(ld))
        p = verify.page_text("https://jobs.ashbyhq.com/x/1", opener=opener_for(body))
        self.assertTrue(p["readable"])
        self.assertIn("Master's students", p["text"])
        self.assertIn("minValue", p["text"])

    def test_workday_json(self):
        body = json.dumps({"jobPostingInfo": {
            "title": "Summer 2027 Intern", "location": "Remote",
            "jobDescription": "<p>%s</p>" % LONG}})
        seen = []
        p = verify.page_text("https://a.wd1.myworkdayjobs.com/en-US/S/job/X/Y_R1",
                             opener=opener_for(body, "application/json", seen))
        self.assertEqual(p["kind"], "workday")
        self.assertIn("/wday/cxs/a/S/job/X/Y_R1", seen[0])
        self.assertTrue(p["readable"])

    def test_js_shell_is_unreadable_not_a_guess(self):
        p = verify.page_text("https://x.example/job",
                             opener=opener_for("<html><body><div id=app></div></body></html>"))
        self.assertFalse(p["readable"])
        self.assertIn("no readable posting text", p["error"])

    def test_network_error_is_a_result(self):
        p = verify.page_text("https://x.example/job",
                             opener=opener_for(urllib.error.URLError("offline")))
        self.assertFalse(p["readable"])
        self.assertIn("URLError", p["error"])


def answer(checks, pay="$45/hr", summary="ok"):
    full = {c: {"ok": True, "evidence": "fine"} for c in verify.CHECKS}
    full.update(checks)
    return {"content": [{"type": "text", "text": "Here:\n" + json.dumps(
        {"checks": full, "pay": pay, "summary": summary})}]}


class LLM(unittest.TestCase):

    def test_all_pass(self):
        sent = []
        v = verify.LLMVerifier("sk-test", transport=lambda p: sent.append(p) or answer({}))
        out = v.verify({"company": "Acme", "title": "SWE Intern", "city": "NYC"}, LONG)
        self.assertIs(out["eligible"], True)
        self.assertEqual(out["pay"], "$45/hr")
        self.assertIn("Posting text:", sent[0]["messages"][0]["content"])
        self.assertNotIn("sk-test", json.dumps(sent[0]))

    def test_one_false_rules_out(self):
        v = verify.LLMVerifier("k", transport=lambda p: answer(
            {"citizenship": {"ok": False, "evidence": "Must be a US citizen"}}))
        out = v.verify({}, LONG)
        self.assertIs(out["eligible"], False)
        self.assertEqual(out["checks"]["citizenship"]["evidence"], "Must be a US citizen")

    def test_silence_is_unknown_not_pass(self):
        v = verify.LLMVerifier("k", transport=lambda p: answer(
            {"sponsorship": {"ok": None, "evidence": ""}}))
        self.assertIsNone(v.verify({}, LONG)["eligible"])

    def test_garbage_answer(self):
        v = verify.LLMVerifier("k", transport=lambda p: {"content": [{"type": "text",
                                                                      "text": "sorry"}]})
        out = v.verify({}, LONG)
        self.assertIsNone(out["eligible"])
        self.assertTrue(out["transient"])

    def test_key_never_in_repr_and_required(self):
        self.assertNotIn("sk-secret", repr(verify.LLMVerifier("sk-secret")))
        with self.assertRaises(ValueError):
            verify.LLMVerifier("")

    def test_name_tracks_model_and_prompt(self):
        a = verify.LLMVerifier("k", model="m1").name
        b = verify.LLMVerifier("k", model="m2").name
        self.assertNotEqual(a, b)


class CachedPath(unittest.TestCase):

    def setUp(self):
        self.cache, self.calls = {}, 0

    def get(self, h, n):
        return self.cache.get((h, n))

    def put(self, h, n, v, url):
        self.cache[(h, n)] = v

    def fetch(self, text):
        return lambda url: {"url": url, "fetched": url, "kind": "html", "text": text,
                            "readable": len(text) >= verify.MIN_TEXT, "error": None}

    def counting(self, checks=None):
        def t(p):
            self.calls += 1
            return answer(checks or {})
        return verify.LLMVerifier("k", transport=t)

    def test_second_call_is_free(self):
        v = self.counting()
        e = {"url": "u"}
        a = verify.verify(e, v, self.get, self.put, fetch=self.fetch(LONG))
        b = verify.verify(e, v, self.get, self.put, fetch=self.fetch(LONG + "  "))
        self.assertEqual(self.calls, 1)
        self.assertFalse(a["cached"])
        self.assertTrue(b["cached"])
        self.assertEqual(a["page_hash"], b["page_hash"])     # whitespace-insensitive

    def test_edited_posting_is_reverified(self):
        v = self.counting()
        verify.verify({"url": "u"}, v, self.get, self.put, fetch=self.fetch(LONG))
        verify.verify({"url": "u"}, v, self.get, self.put,
                      fetch=self.fetch(LONG + " US citizens only."))
        self.assertEqual(self.calls, 2)

    def test_unreadable_is_not_cached_or_paid_for(self):
        v = self.counting()
        out = verify.verify({"url": "u"}, v, self.get, self.put, fetch=self.fetch("tiny"))
        self.assertFalse(out["readable"])
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.cache, {})

    def test_transient_failure_is_not_cached(self):
        def boom(p):
            raise urllib.error.URLError("down")
        v = verify.LLMVerifier("k", transport=boom)
        out = verify.verify({"url": "u"}, v, self.get, self.put, fetch=self.fetch(LONG))
        self.assertIsNone(out["eligible"])
        self.assertEqual(self.cache, {})

    def test_null_verifier(self):
        out = verify.verify({"url": "u"}, verify.NullVerifier(), self.get, self.put,
                            fetch=self.fetch(LONG))
        self.assertIsNone(out["eligible"])
        self.assertEqual(self.cache, {})


if __name__ == "__main__":
    unittest.main()
