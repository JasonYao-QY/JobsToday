"""Per-posting eligibility, read from the posting itself.

The feed cannot see sponsorship, citizenship, class standing, graduation
window or pay (proposal §3.1, §3.2). Those are exactly what wastes his
time, so the last word on eligibility comes from the posting's own text.

Shape:
  page_text(url)          fetch a posting and reduce it to plain text
  Verifier                interface: verify(listing_entry, text) -> verdict
  NullVerifier            says "unknown" to everything; free; the default
  LLMVerifier             asks a model; the API key lives here and nowhere else
  verify(...)             fetch, hash, look up the cache, call, store

The cache is keyed on a hash of the *fetched text* plus the verifier's
identity (implementation, model, prompt version), so an edited posting or a
changed prompt never serves a stale verdict (proposal §5).

A verdict:
  {eligible: True | False | None,        None = could not tell
   checks: {term, degree, graduation, class_standing, sponsorship,
            citizenship, location, clearance}: {ok: bool|None, evidence: str},
   pay: str | None, summary: str, verifier: str}
"""

import hashlib
import html
import html.parser
import json
import re
import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "CHECKS",
    "Verifier",
    "NullVerifier",
    "LLMVerifier",
    "page_url",
    "page_text",
    "text_hash",
    "verify",
]

CHECKS = ("term", "degree", "graduation", "class_standing", "sponsorship",
          "citizenship", "location", "clearance")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Below this, a page is a JavaScript shell, not a posting. A verdict on it
# would be a guess, so it is reported unreadable instead.
MIN_TEXT = 400


# --------------------------------------------------------------------------
# fetching

_WORKDAY = re.compile(
    r"^https://(?P<host>(?P<tenant>[\w-]+)\.wd\d+\.myworkdayjobs\.com)/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[^/?#]+)/(?P<rest>job/[^?#]+)")


def page_url(url):
    """(fetch_url, kind). Workday postings are rendered by JavaScript;
    their public JSON endpoint carries the same description, so fetch that.
    Everything else is fetched as HTML."""
    m = _WORKDAY.match(url or "")
    if m:
        return ("https://%s/wday/cxs/%s/%s/%s" % (
            m.group("host"), m.group("tenant"), m.group("site"), m.group("rest")),
            "workday")
    return url, "html"


class _Text(html.parser.HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
              "h6", "tr", "section", "article", "header", "footer", "td"}

    def __init__(self):
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.skip:
            self.skip -= 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(markup):
    p = _Text()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:           # HTMLParser is lenient; this is belt and braces
        pass
    text = "".join(p.out)
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def _json_ld_text(markup):
    """schema.org JobPosting description, which many ATS pages embed even
    when the visible page is client-rendered."""
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                         markup or "", re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                out.append(html_to_text(html.unescape(d.get("description") or "")))
                if d.get("baseSalary"):
                    out.append("Salary: " + json.dumps(d["baseSalary"]))
    return "\n".join(x for x in out if x)


def page_text(url, timeout=30, opener=None):
    """Fetch a posting -> {url, fetched, kind, text, readable, error}.
    Never raises; a failure is a result the UI can show."""
    fetch_url, kind = page_url(url)
    opener = opener or urllib.request.urlopen
    headers = {"User-Agent": _UA,
               "Accept": "application/json" if kind == "workday" else "text/html,*/*"}
    out = {"url": url, "fetched": fetch_url, "kind": kind, "text": "",
           "readable": False, "error": None}
    try:
        with opener(urllib.request.Request(fetch_url, headers=headers), timeout=timeout) as r:
            raw = r.read(3_000_000)
            charset = "utf-8"
            ct = r.headers.get("Content-Type", "") if r.headers else ""
            m = re.search(r"charset=([\w-]+)", ct)
            if m:
                charset = m.group(1)
        body = raw.decode(charset, "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out

    if kind == "workday":
        try:
            info = json.loads(body).get("jobPostingInfo") or {}
        except (ValueError, AttributeError):
            info = {}
        parts = [info.get("title") or "", info.get("location") or "",
                 " ".join(info.get("additionalLocations") or []),
                 info.get("timeType") or "", info.get("startDate") or "",
                 html_to_text(info.get("jobDescription") or "")]
        text = "\n".join(p for p in parts if p)
    else:
        text = _json_ld_text(body)
        visible = html_to_text(body)
        if len(visible) > len(text):
            text = text + "\n" + visible if text else visible
    out["text"] = text.strip()
    out["readable"] = len(out["text"]) >= MIN_TEXT
    if not out["readable"] and not out["error"]:
        out["error"] = "Page has no readable posting text (%d chars)." % len(out["text"])
    return out


def text_hash(text):
    norm = " ".join((text or "").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# verifiers

def _unknown(summary, verifier):
    return {"eligible": None,
            "checks": {c: {"ok": None, "evidence": ""} for c in CHECKS},
            "pay": None, "summary": summary, "verifier": verifier}


class Verifier(object):
    """Interface. `name` must change whenever the verdict for the same text
    could change (model, prompt), because it is half of the cache key."""

    name = "abstract"

    def verify(self, entry, text):
        raise NotImplementedError


class NullVerifier(Verifier):
    """Reads nothing, decides nothing. The default until he opts in to
    paying for LLM verification, and the stand-in for tests."""

    name = "null/1"

    def verify(self, entry, text):
        return _unknown("Not verified.", self.name)


_PROMPT_VERSION = 1

_SYSTEM = """You check internship postings against one candidate's eligibility.
Answer only from the posting text. Quote short evidence. If the text does not
say, the answer is null, not a guess.

The candidate:
- Master's student (MSE, Carnegie Mellon), graduating December 2027.
- On an F-1 visa: needs CPT/OPT; cannot accept roles requiring US citizenship,
  permanent residency, a security clearance, or "no sponsorship now or in future".
- Wants a Summer 2027 internship in the United States. Not a January, spring,
  fall or multi-season co-op start.

Return one JSON object and nothing else:
{"checks": {
   "term":           {"ok": true|false|null, "evidence": "..."},
   "degree":         {"ok": ..., "evidence": "..."},   // Master's students eligible?
   "graduation":     {"ok": ..., "evidence": "..."},   // window includes Dec 2027?
   "class_standing": {"ok": ..., "evidence": "..."},   // no freshman/sophomore/junior/undergrad-only/early-career gate?
   "sponsorship":    {"ok": ..., "evidence": "..."},   // F-1 CPT/OPT acceptable?
   "citizenship":    {"ok": ..., "evidence": "..."},   // no citizenship/GC requirement?
   "location":       {"ok": ..., "evidence": "..."},   // a US location?
   "clearance":      {"ok": ..., "evidence": "..."}},  // no clearance / defense requirement?
 "pay": "hourly or salary range as written, or null",
 "summary": "one sentence"}
ok=true means the candidate passes that check; false means the text rules him out.
"""


class LLMVerifier(Verifier):
    """Anthropic Messages API over urllib (no SDK; the core stays
    dependency-free). The key is passed in by batch.py from the jobs-only
    setting and is never logged, stored or returned."""

    def __init__(self, api_key, model="claude-haiku-4-5-20251001", transport=None,
                 max_chars=24000, timeout=60):
        if not api_key:
            raise ValueError("LLMVerifier needs an API key")
        self._key = api_key
        self.model = model
        self.max_chars = max_chars
        self.timeout = timeout
        self._transport = transport or self._post
        self.name = "llm/%s/p%d" % (model, _PROMPT_VERSION)

    def __repr__(self):
        return "LLMVerifier(model=%r)" % self.model         # never the key

    def _post(self, payload):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-api-key": self._key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def verify(self, entry, text):
        user = "Company: %s\nRole: %s\nListed cities: %s\n\nPosting text:\n%s" % (
            entry.get("company"), entry.get("title"),
            entry.get("city") or "", (text or "")[:self.max_chars])
        payload = {"model": self.model, "max_tokens": 900, "system": _SYSTEM,
                   "messages": [{"role": "user", "content": user}]}
        try:
            resp = self._transport(payload)
        except (urllib.error.URLError, OSError, ValueError) as e:
            v = _unknown("Verifier call failed: %s." % type(e).__name__, self.name)
            v["transient"] = True       # do not cache
            return v
        return self.parse(resp)

    def parse(self, resp):
        try:
            text = "".join(b.get("text", "") for b in resp.get("content", [])
                           if b.get("type") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else None
        except (AttributeError, ValueError, TypeError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
            v = _unknown("Verifier answer was not readable.", self.name)
            v["transient"] = True
            return v
        checks = {}
        for c in CHECKS:
            x = data["checks"].get(c) or {}
            ok = x.get("ok") if isinstance(x, dict) else None
            checks[c] = {"ok": ok if ok in (True, False) else None,
                         "evidence": str((x.get("evidence") if isinstance(x, dict) else "") or "")[:300]}
        oks = [checks[c]["ok"] for c in CHECKS]
        eligible = False if False in oks else (True if all(o is True for o in oks) else None)
        pay = data.get("pay")
        return {"eligible": eligible, "checks": checks,
                "pay": pay if isinstance(pay, str) and pay.strip() else None,
                "summary": str(data.get("summary") or "")[:300], "verifier": self.name}


# --------------------------------------------------------------------------
# the cached path

def verify(entry, verifier, cache_get, cache_put, fetch=None):
    """entry: a rank.score() entry (needs url, company, title, city).
    cache_get(hash, name) / cache_put(hash, name, verdict, url) are bound
    to state.get_verdict / put_verdict by batch.py.

    Returns the verdict plus {page_hash, cached, readable, error}. An
    unreadable page or a failed call is never cached, so it is retried on
    the next run rather than remembered as "unknown" forever."""
    fetch = fetch or page_text
    page = fetch(entry.get("url") or "")
    if not page["readable"]:
        v = _unknown(page["error"] or "Page unreadable.", verifier.name)
        v.update(page_hash=None, cached=False, readable=False, error=page["error"])
        return v
    h = text_hash(page["text"])
    hit = cache_get(h, verifier.name)
    if hit is not None:
        hit = dict(hit, page_hash=h, cached=True, readable=True, error=None)
        return hit
    v = verifier.verify(entry, page["text"])
    if not v.pop("transient", False) and verifier.name != NullVerifier.name:
        cache_put(h, verifier.name, v, entry.get("url"))
    v.update(page_hash=h, cached=False, readable=True, error=None)
    return v
