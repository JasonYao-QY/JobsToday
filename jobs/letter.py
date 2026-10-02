# -*- coding: utf-8 -*-
"""Cover letters: one posting, plus the facts on the resume he is actually sending, in his own voice.

Public: Writer, problems, load_profile, PROFILE_NAME.

Two things make this different from asking a chat window for a cover letter.

It is grounded. The prompt carries only the bullets from the one resume variant the ranker chose for this
row, so the letter cannot claim a project that is not on the page the reader is holding. The variant's
own contents come from a file he keeps beside the exclusion seed; without it there is nothing to ground
on and the writer refuses rather than inventing a career.

It is checked. `problems()` is a plain function over the finished text that looks for the things he keeps
striking out of drafts: dashes standing in for clauses, stock openers, and the three-item rhythm that
reads as machine-written. A draft that trips it is sent back once with the list attached, and whatever
comes back is handed over either way, with the remaining problems named, because a draft he can edit
beats a spinner.

No key handling lives here: the caller passes one in, exactly as it does for verify.LLMVerifier.
"""

import json
import os
import re
import urllib.error
import urllib.request

__all__ = ["Writer", "problems", "load_profile", "PROFILE_NAME", "PROMPT_VERSION"]


PROFILE_NAME = "resume.json"
PROMPT_VERSION = 1

MIN_WORDS, MAX_WORDS = 150, 420

# Phrases that announce a machine wrote it. Lowercase, matched as substrings.
STOCK = (
    "i am excited to", "i'm excited to", "excited to apply", "thrilled to",
    "i am writing to express", "i am writing to apply", "writing to express my interest",
    "passionate about", "deeply passionate", "my passion for",
    "aligns perfectly", "align perfectly", "perfect fit", "ideal candidate",
    "i believe i would be", "i am confident that i", "proven track record",
    "leverage my", "delve into", "deep dive into", "wealth of experience",
    "cutting-edge", "state-of-the-art", "fast-paced environment",
    "i would welcome the opportunity", "thank you for your time and consideration",
    "it is worth noting", "needless to say", "last but not least",
)

_DASH = re.compile(r"[—–]|(?<!\w)--(?!\w)|\s--\s")
# "a, b, and c" inside one sentence: ordinary once, a tic when it keeps happening.
_TRIAD = re.compile(r"[^.;!?\n]+,[^.;!?\n]+,\s+and\s+[^.;!?\n]+[.;!?]")


def problems(text):
    """Everything about a draft that breaks his house style -> list of short strings; empty means clean.

    Deliberately mechanical. It cannot judge whether the letter is any good, only whether it carries the
    marks he always removes, which is the part a model will reproduce unless something checks.
    """
    out = []
    t = text or ""
    low = t.lower()
    if _DASH.search(t):
        out.append("em dash, en dash or double hyphen used instead of rewriting the clause")
    hits = sorted({p for p in STOCK if p in low})
    if hits:
        out.append("stock phrasing: " + ", ".join('"%s"' % h for h in hits[:4]))
    if len(_TRIAD.findall(t)) > 1:
        out.append("parallel three-item lists more than once; keep at most one")
    n = len(t.split())
    if n < MIN_WORDS:
        out.append("too short at %d words; aim for 250 to 350" % n)
    elif n > MAX_WORDS:
        out.append("too long at %d words; aim for 250 to 350" % n)
    return out


def load_profile(path):
    """The resume facts -> dict, or None when the file is missing or unusable.

    Shape: {"name": str, "shared": [str], "variants": {"SWE": [str], ...}}. `shared` is what every variant
    carries (education, dates, the roles themselves); `variants` is what distinguishes them.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("variants"), dict):
        return None
    return data


def facts_for(profile, variant):
    """The lines a letter for `variant` may draw on: the shared ones plus that variant's own.

    An unknown variant gets the shared lines only. Better a thin letter than one that borrows bullets
    from a resume he is not sending.
    """
    shared = [s for s in (profile.get("shared") or []) if isinstance(s, str)]
    own = [s for s in (profile.get("variants", {}).get(variant) or []) if isinstance(s, str)]
    return shared + own


SYSTEM = """You draft a cover letter as Qingyuan Yao, in his own voice. You are given the posting and the
exact contents of the one resume he is sending with it.

Hard rules.
- Every claim must be supported by the resume facts given. Never invent an employer, a project, a number,
  a technology or a date. If the posting asks for something he does not have, leave it out; do not gesture
  at it.
- No em dashes, en dashes or double hyphens anywhere. If a sentence wants one, rewrite the sentence.
- No stock cover-letter phrasing: not "excited to apply", not "writing to express my interest", not
  "passionate about", not "aligns perfectly", not "proven track record", not "I would welcome the
  opportunity".
- Use at most one three-item list in the whole letter. Varying sentence length matters more than rhythm;
  do not build paragraphs out of parallel clauses.
- Plain, specific, first person. Short words. Say what he did and what happened, with the real numbers
  from the resume facts. One concrete thing from the posting itself, so it is clearly not a form letter.

Shape. 250 to 350 words. "Dear Hiring Team," unless the posting names someone. Three or four short
paragraphs: why this role at this company, the one or two pieces of his experience that bear on it with
their numbers, availability, a one-line close. Sign "Qingyuan Yao".

Return the letter only. No preamble, no notes, no markdown headings."""

def api_error(e):
    """An HTTPError from the Messages API, as a sentence worth showing.

    urllib puts the reason in the response body and leaves it out of str(e), so "HTTP Error 400: Bad
    Request" is all the tab would otherwise get -- true and useless. The body says whether it is a bad
    model name, an expired key or an empty credit balance.
    """
    body = ""
    try:
        body = e.read().decode("utf-8", "replace").strip()
    except Exception:                                        # noqa: BLE001 - never hide the real error
        pass
    try:
        import json as _json
        msg = _json.loads(body)["error"]["message"]
    except Exception:                                        # noqa: BLE001
        msg = body[:300]
    return "HTTP %s%s" % (getattr(e, "code", "?"), ": " + msg if msg else "")



class Writer(object):
    """Anthropic Messages API over urllib, like verify.LLMVerifier: no SDK, and the key is held here and
    never logged, stored or returned."""

    def __init__(self, api_key, model="claude-opus-5", transport=None, max_chars=18000, timeout=120):
        if not api_key:
            raise ValueError("Writer needs an API key")
        self._key = api_key
        self.model = model
        self.max_chars = max_chars
        self.timeout = timeout
        self._transport = transport or self._post
        self.name = "llm/%s/l%d" % (model, PROMPT_VERSION)

    def __repr__(self):
        return "Writer(model=%r)" % self.model              # never the key

    def _post(self, payload):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-api-key": self._key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _ask(self, messages):
        payload = {"model": self.model, "max_tokens": 1400, "system": SYSTEM, "messages": messages}
        resp = self._transport(payload)
        parts = [b.get("text", "") for b in (resp.get("content") or []) if b.get("type") == "text"]
        return "\n".join(parts).strip()

    def write(self, entry, posting_text, profile, variant):
        """-> {"text", "variant", "words", "problems", "retried", "model", "error"}

        A draft that trips `problems` is sent back once with the list attached. The second answer is kept
        whatever it looks like: its remaining problems travel with it so the tab can show them, and he can
        fix three words faster than a third round trip can.
        """
        out = {"text": "", "variant": variant, "words": 0, "problems": [], "retried": False,
               "model": self.model, "error": None}
        facts = facts_for(profile or {}, variant)
        if not facts:
            out["error"] = "no resume facts for variant %r" % variant
            return out

        user = (
            "Company: %s\nRole: %s\nLocation: %s\nPosting URL: %s\n\n"
            "Resume he is sending (variant %s). Every claim must come from this list:\n%s\n\n"
            "Posting text:\n%s"
            % (entry.get("company") or "", entry.get("title") or "", entry.get("city") or "",
               entry.get("url") or "", variant,
               "\n".join("- " + f for f in facts), (posting_text or "")[:self.max_chars]))
        messages = [{"role": "user", "content": user}]
        try:
            text = self._ask(messages)
            bad = problems(text)
            if bad and text:
                out["retried"] = True
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                        "That draft breaks the rules in these ways:\n%s\n\nRewrite it. Keep what is "
                        "specific and true, fix only what is listed, and return the letter only."
                        % "\n".join("- " + b for b in bad)}]
                text = self._ask(messages) or text
        except urllib.error.HTTPError as e:
            out["error"] = api_error(e)                      # before URLError: HTTPError is a subclass
            return out
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out

        out["text"] = text
        out["words"] = len(text.split())
        out["problems"] = problems(text)
        return out
