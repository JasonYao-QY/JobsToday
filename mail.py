# -*- coding: utf-8 -*-
"""Mail: reading Gmail over IMAP, classifying job mail, and the scan that feeds the inbox and the application
tracker. The only module that handles mail credentials (app passwords in the macOS Keychain) and message bodies.

The phrase tables, classify(), company_from() and extract_due() are tuned against real mail; change them only
with a fixture test that shows why (tests/fixtures/mail_samples.json).

Public: scan_all, add_account, has_password, remove_account, LoginFailed, load_config, save_config,
classify, company_from, extract_due, extract_minutes, clean_subject, KEYCHAIN_SERVICE (and CONFIG_FILE).
Passwords never leave this module: callers add, check and remove accounts through the functions above.
"""

import datetime as dt
import html as htmlmod
import os
import re
import ssl
import subprocess
import urllib.parse

from inbox import load_inbox, merge_inbox, save_inbox
from store import APP_DIR, load_json, new_id, save_json
from textutil import clip
from timeutil import MON3, fmt_minutes, fmt_when, now, short_date, to_date, today_str


CONFIG_FILE = os.path.join(APP_DIR, "config.json")


KEYCHAIN_SERVICE = "today-mail"


def load_config():
    c = load_json(CONFIG_FILE, {}) or {}
    c.setdefault("accounts", [])
    c.setdefault("hosts", {})
    c.setdefault("scan_days", 30)
    c.setdefault("history_since", "2026-06-01")
    return c


def save_config(c):
    save_json(CONFIG_FILE, c)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def _security(args):
    try:
        r = subprocess.run(["security"] + args, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired):
        return None, "", ""


def password_get(account, cfg):
    rc, out, _ = _security(["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"])
    if rc == 0 and out.strip():
        return out.strip()
    return (cfg.get("passwords") or {}).get(account)


def password_set(account, pw, cfg):
    rc, _, _ = _security(["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", pw])
    if rc == 0:
        return "keychain"
    cfg.setdefault("passwords", {})[account] = pw
    save_config(cfg)
    return "config"


def password_del(account, cfg):
    _security(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account])
    if (cfg.get("passwords") or {}).pop(account, None) is not None:
        save_config(cfg)


class LoginFailed(Exception):
    """Gmail refused the IMAP login; str(e) is the server's reason on one line."""


def add_account(email, app_password):
    """Test an app password over IMAP, then store it (Keychain, else config.json with mode 600) and add the
    account. -> "keychain" | "config". Raises ValueError for unusable input, LoginFailed if Gmail says no."""
    acct = (email or "").strip().lower()
    if "@" not in acct:
        raise ValueError("enter an email address")
    pw = (app_password or "").replace(" ", "").strip()
    if not pw:
        raise ValueError("paste the app password")
    cfg = load_config()
    import imaplib
    host = cfg["hosts"].get(acct, "imap.gmail.com")
    try:
        M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=30)
        M.login(acct, pw)
        M.logout()
    except Exception as e:
        raise LoginFailed(re.sub(r"\s+", " ", str(e)))
    where = password_set(acct, pw, cfg)
    if acct not in cfg["accounts"]:
        cfg["accounts"].append(acct)
    save_config(cfg)
    return where


def has_password(account):
    return bool(password_get(account, load_config()))


def remove_account(email):
    cfg = load_config()
    acct = (email or "").strip().lower()
    password_del(acct, cfg)
    cfg["accounts"] = [a for a in cfg["accounts"] if a != acct]
    save_config(cfg)
    return acct


SKIP_DOMAINS = ("linkedin.com", "indeed.com", "glassdoor", "simplify.jobs", "levels.fyi", "wellfound", "angel.co",
                "github.com", "piazza.com", "instructure.com", "medium.com", "substack", "ziprecruiter", "monster.com",
                "dice.com", "builtin.com", "hired.com", "ripplematch", "wayup.com", "jobright", "joinhandshake",
                "candidate.fyi", "devlog.to", "codedrill.com", "rentcafe", "calendar-notification", "paypal.com", "venmo",
                "uber.com", "doordash", "spotify", "netflix", "youtube", "facebookmail", "instagram", "twitter",
                "discord", "steampowered", "epicgames", "riotgames")


PLATFORM_DOMAINS = {"hackerrank": "HackerRank", "codesignal": "CodeSignal", "codility": "Codility",
                    "criteriacorp": "Criteria", "ondemandassessment": "Criteria", "canditech": "Canditech",
                    "hirevue": "HireVue", "modernhire": "HireVue", "hvue": "HireVue", "karat": "Karat",
                    "coderpad": "CoderPad", "testgorilla": "TestGorilla", "pymetrics": "pymetrics",
                    "codingame": "CodinGame", "byteboard": "Byteboard", "glider.ai": "Glider", "harver": "Harver",
                    "woventeams": "Woven", "codeinterview": "CodeInterview", "hackerearth": "HackerEarth",
                    "coderbyte": "Coderbyte", "alooba": "Alooba", "vervoe": "Vervoe", "plum.io": "Plum"}


PLATFORM_WORDS = {"hackerrank": "HackerRank", "codesignal": "CodeSignal", "codility": "Codility",
                  "hirevue": "HireVue", "canditech": "Canditech", "karat": "Karat", "coderpad": "CoderPad",
                  "criteria assessment": "Criteria", "ccat": "Criteria", "pymetrics": "pymetrics",
                  "hackerearth": "HackerEarth", "testgorilla": "TestGorilla", "byteboard": "Byteboard"}


ATS_WORDS = ("workday", "greenhouse", "lever", "icims", "ashby", "smartrecruiters", "jobvite", "taleo", "gem",
             "oracle", "successfactors", "phenom", "eightfold", "rippling", "workable", "bamboohr", "breezy",
             "recruitee", "jazzhr", "ultipro", "avature", "paylocity", "brassring", "dover", "canditech",
             "hackerrank", "criteria", "hirevue", "modernhire", "codesignal", "codility", "karat")


REJECT_STRONG = ["not moving forward", "not be moving forward", "won't be moving forward", "will not be moving forward",
                 "not to move forward", "decided not to move forward", "not move forward with your",
                 "move forward with other", "moving forward with other", "pursue other candidates", "other candidates",
                 "another candidate", "not selected", "not been selected", "weren't selected", "were not selected",
                 "regret to inform", "we regret", "unable to move you forward", "unable to offer", "not able to offer",
                 "not be proceeding", "not proceed to next steps", "not to proceed", "position has been filled",
                 "no longer under consideration", "not a match", "not the right fit", "will not be progressing",
                 "not progressing", "unsuccessful", "not successful", "not chosen", "didn't work out",
                 "did not work out", "not be able to move forward", "closed the position", "filled the position",
                 "not selected for further", "no longer being considered", "decided not to proceed"]


REJECT_WEAK = ["unfortunately", "after careful consideration", "after careful review", "after carefully reviewing",
               "difficult decision", "never easy news", "not what you wanted to hear", "wish you the best",
               "wish you continued success", "wish you success", "keep your resume on file",
               "keep your information on file", "future opportunities", "other positions that match",
               "encourage you to apply", "continue to visit our careers"]


OFFER_PHRASES = ["offer letter", "pleased to offer", "excited to offer", "delighted to offer", "extend an offer",
                 "extend you an offer", "offer of employment", "formal offer", "would like to offer you",
                 "thrilled to offer", "happy to offer you"]


INTERVIEW_PHRASES = ["interview invitation", "invitation to interview", "invite you to interview",
                     "invite you to an interview", "invited to interview", "schedule your interview",
                     "schedule an interview", "schedule a time", "schedule time", "schedule a call", "phone screen",
                     "phone interview", "video interview", "virtual interview", "technical interview",
                     "technical screen", "first round", "next round", "final round", "onsite", "on-site", "superday",
                     "super day", "your availability", "calendly.com", "book a time", "pick a time", "meet with",
                     "chat with you", "speak with you", "talk with you", "love to connect", "set up a time",
                     "recruiter screen", "coffee chat", "hiring manager would like", "conversation with"]


INTERVIEW_NOT = ("assessment", "feedback", "tips", "prep", "guide", "survey", "how to", "questions to ask",
                 "newsletter", "webinar", "recording")


OA_WORDS = ["assessment", "coding challenge", "coding test", "online test", "take-home", "take home",
            "technical challenge", "hackerrank", "codesignal", "codility", "hirevue", "coding exercise",
            "coding assignment", "aptitude test", "online evaluation", "pre-interview", "video response"]


OA_DONE = ["thanks for taking", "thank you for completing", "thanks for completing", "submission confirmation",
           "successfully submitted", "we've received your submission", "we have received your submission",
           "has been submitted", "you have completed", "completed the assessment", "assessment completed",
           "assessments completed", "received everything successfully", "test has been submitted",
           "sent your submission", "your submission has been"]


APPLIED_PHRASES = ["thank you for applying", "thanks for applying", "thank you for your application",
                   "thanks for your application", "application received", "we have received your application",
                   "we've received your application", "received your application", "application has been received",
                   "application has been submitted", "successfully submitted your application", "we've got it",
                   "application is underway", "confirm your application", "application confirmation",
                   "still reviewing applications", "thanks for completing your application",
                   "thank you for your interest in", "thanks for your interest in", "thanks again for your interest",
                   "for applying", "time to apply", "officially applied", "submitting your application",
                   "submitted your application", "received your submission", "received your resume",
                   "your application to", "your application for", "application is in", "expressing interest",
                   "expressing your interest"]


APPLIED_WEAK = ("thank you for your interest in", "thanks for your interest in", "thanks again for your interest",
                "expressing interest", "expressing your interest", "your application to", "your application for")


# an application confirmation has to look like a job (not a credit card, lease or school application)
JOB_EVIDENCE = ("position", "role", "intern", " job", "career", "recruit", "hiring", "candida", "opening", "talent",
                "resume", " cv", "applying to", "apply to", "program")


IMPORTANT_SUBJECT = ["action required", "action needed", "action requested", "urgent", "deadline", "due today",
                     "due tomorrow", "final reminder", "final notice", "past due", "overdue", "expires", "expiring",
                     "time sensitive", "time-sensitive", "immediate attention", "response required",
                     "response requested", "reply needed", "please confirm", "confirmation required", "rsvp",
                     "last day to", "reminder:", "important:"]


CODE_WORDS = ("login code", "verification code", "security code", "one-time", "one time code", "passcode", "otp")


JOB_WORDS = ("applicat", "applying", "applied", "candida", "position", "role", "interview", "recruit", "hiring",
             "internship", "job", "career", "opportunit", "talent", "assessment")


def job_context(text):
    return any(w in text for w in JOB_WORDS)


def platform_of(dom, from_name, subj, body=""):
    hay = dom + " " + from_name
    for k, v in PLATFORM_DOMAINS.items():
        if k in hay:
            return v
    for k, v in PLATFORM_WORDS.items():
        if k in subj:
            return v
    return ""


def platform_words(text):
    for k, v in PLATFORM_WORDS.items():
        if k in text:
            return v
    return ""


def is_interview(subj, head):
    if "interview" in subj and not any(w in subj for w in INTERVIEW_NOT):
        return True
    return any(p in subj or p in head for p in INTERVIEW_PHRASES)


CONDITIONAL = ("if ", "should ", "unless ", "whether ", "in case ", "when ", "once ")


def phrase_hits(phrases, text):
    """Count phrase occurrences that are not inside a conditional clause ("if you're not selected…")."""
    n = 0
    for p in phrases:
        start = 0
        while True:
            k = text.find(p, start)
            if k < 0:
                break
            before = text[max(0, k - 40): k]
            if not any(c in before for c in CONDITIONAL):
                n += 1
            start = k + len(p)
    return n


def classify(hd, body):
    """-> (kind or None, platform)"""
    subj = hd["subject"].lower()
    head = body[:900].lower()
    both = subj + " \n " + head
    dom = hd["from_addr"].split("@")[-1]
    name = hd["from_name"].lower()
    if any(d in dom for d in SKIP_DOMAINS):
        return None, ""
    if any(w in subj for w in CODE_WORDS):
        return None, ""
    platform = platform_of(dom, name, subj)
    if platform and any(k in dom or k in name for k in PLATFORM_DOMAINS):
        if any(p in both for p in OA_DONE):
            return "oa_done", platform
        return "oa", platform
    if job_context(both):
        if phrase_hits(REJECT_STRONG, both) or phrase_hits(REJECT_WEAK, head) >= 2:
            return "rejection", ""
        if any(p in both for p in OFFER_PHRASES):
            return "offer", ""
        if is_interview(subj, head):
            return "interview", ""
        if any(p in both for p in OA_DONE) and any(w in both for w in OA_WORDS):
            return "oa_done", platform or platform_words(both)
        if any(w in subj for w in OA_WORDS) or (
                any(w in head for w in OA_WORDS) and any(w in head for w in ("invite", "complete", "next step", "please take", "next stage"))):
            return "oa", platform or platform_words(both)
        hits = [p for p in APPLIED_PHRASES if p in both]
        if hits and any(w in both for w in JOB_EVIDENCE) \
                and not (hd.get("unsub") and all(p in APPLIED_WEAK for p in hits)):
            return "applied", ""
    if not hd.get("unsub") and any(p in subj for p in IMPORTANT_SUBJECT):
        return "important", ""
    return None, ""


GENERIC_NAME_WORDS = {"recruiting", "recruitment", "recruiter", "recruiters", "careers", "career", "talent",
                      "acquisition", "team", "hr", "people", "hiring", "jobs", "campus", "university", "notifications",
                      "notification", "noreply", "no-reply", "do-not-reply", "donotreply", "support", "info", "admin",
                      "via", "global", "north", "america", "americas", "early", "early-careers", "assessments",
                      "assessment", "event", "events", "applications", "application", "talentteam"}


CONNECTOR_WORDS = {"the", "of", "and", "&", "at", "for", "to", "in", "on", "a", "an"}


ROLE_WORDS = {"software", "engineer", "engineers", "engineering", "intern", "interns", "internship", "internships",
              "summer", "quant", "quantitative", "coding", "assessment", "assessments", "data", "product", "program",
              "programme", "your", "this", "please", "thank", "thanks", "hi", "hello", "dear", "online", "technical",
              "interview", "application", "applications", "position", "role", "team", "candidate", "recruiting",
              "campus", "university", "machine", "learning", "analyst", "developer", "development", "research",
              "scientist", "we", "our", "us", "you", "it", "if", "next", "step", "steps", "update", "invitation",
              "invite", "reminder", "action", "required", "phd", "masters", "remote", "test", "challenge", "digital",
              "cloud", "ai", "ml", "sde", "swe", "risk", "technology", "welcome", "congratulations", "important",
              "hackerrank", "hirevue", "criteria", "canditech", "workday", "greenhouse", "pre-interview", "prescreen",
              "complete", "completing", "taking", "re", "fwd", "fw", "full-time", "full", "time", "part-time",
              "graduate", "students", "student", "job", "jobs", "beginner", "advanced", "event", "events",
              "coding", "quantitative", "associate", "analytics", "modeling", "system", "systems", "platform",
              "backend", "frontend", "infrastructure", "security", "mobile", "web", "embedded", "robotics", "2026",
              "2027", "2026-2027", "us", "usa", "uk", "york", "chicago", "san", "francisco", "seattle", "austin",
              "boston", "nyc", "ny", "ca", "tx", "il", "wa", "ma", "nc", "your", "you're", "we're", "thanks!"}


PLACE_WORDS = {"york", "chicago", "san", "francisco", "seattle", "austin", "boston", "nyc", "ny", "ca", "tx", "il",
               "wa", "ma", "nc", "us", "usa", "uk"}


def tidy_company(s):
    s = re.sub(r"[^\w&'’.\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—|:,.!")
    s = re.sub(r"^(?:the)\s+", "", s, flags=re.I)
    words = []
    for w in s.split():
        lw = w.lower().strip("!.,")
        if words and (lw in ROLE_WORDS or lw in GENERIC_NAME_WORDS):
            break
        words.append(w)
    while words and words[-1].lower().strip("!.,") in (GENERIC_NAME_WORDS | CONNECTOR_WORDS):
        words.pop()
    return " ".join(words).strip(" -–—|:,.!")[:32]


def plausible_company(s):
    if not s or len(s) < 2:
        return False
    w = [x.lower().strip("!.,") for x in s.split()]
    if (w[0] in ROLE_WORDS and not (w[0] in PLACE_WORDS and len(w) > 1)) or w[0] in CONNECTOR_WORDS:
        return False  # ("Lakeside Trading Company" is a company; "Chicago SWE" isn't)
    if all(x in ROLE_WORDS or x in GENERIC_NAME_WORDS or x in CONNECTOR_WORDS for x in w):
        return False
    if "@" in s or "http" in s.lower() or re.fullmatch(r"[\d\W]+", s):
        return False
    return True


CAP = r"[A-Z][\w&'’.\-]*"


COMPANY_SUBJECT_PATTERNS = [
    r"^(.+?)\s+invites? you",
    r"[Nn]ext [Ss]teps?\s+(?:with|at|for)\s+([^:|–\-]+?)(?:\s*[:|–\-]|$)",
    r"[Uu]pdate from\s+(.+)$",
    r"[Tt]hank you from\s+(.+)$",
    r"^(" + CAP + r"(?:\s+" + CAP + r"){0,3})\s*[|:]",
    r"(?:[Aa]pplying (?:to|at|for)|[Aa]pplication (?:to|at|with|for)|[Ii]nterest in|[Jj]oining|[Pp]osition at|"
    r"[Rr]ole at|[Ii]ntern(?:ship)? at|[Ii]nvit(?:e|ation) to(?: take| complete)?|\bat|\bwith)\s+(?:the\s+)?"
    r"(" + CAP + r"(?:\s+(?:" + CAP + r"|of|and|&)){0,3})",
    r"^(" + CAP + r"(?:\s+" + CAP + r"){0,2})\s+(?:[Aa]pplication|[Cc]oding|[Oo]nline|[Ee]vent|[Aa]ssessments?|"
    r"[Ii]nterview|[Cc]areers|[Rr]ecruiting|[Tt]alent)",
]


COMPANY_BODY_PATTERNS = [
    r"(?:interest in|applying to|applying for|application (?:to|with|at|for)|invited by|on behalf of|joining|join|"
    r"position (?:at|with|here at)|role (?:at|with|here at)|team at|here at|career with|careers at|opportunity at|"
    r"opportunities at|internship at|considering|submission to|submitted to|candidacy (?:at|with)|for)\s+"
    r"(?:a\s+(?:career|role|position)\s+(?:with|at)\s+)?(?:the\s+)?(" + CAP + r"(?:\s+(?:" + CAP + r"|of|and|&)){0,3})",
]


def company_from(hd, body, platform=""):
    name = tidy_company(hd["from_name"] or "")
    dom = hd["from_addr"].split("@")[-1]
    local = hd["from_addr"].split("@")[0]
    if name and plausible_company(name) and not any(k in name.lower() for k in ATS_WORDS) \
            and not any(k in name.lower() for k in ("no-reply", "noreply", "do not reply", "notification")):
        return name

    def from_subject():
        for pat in COMPANY_SUBJECT_PATTERNS:
            for m in re.finditer(pat, hd["subject"]):
                c = tidy_company(m.group(1))
                if plausible_company(c):
                    return c

    def from_body():
        for pat in COMPANY_BODY_PATTERNS:
            for m in re.finditer(pat, body[:1500]):
                c = tidy_company(m.group(1))
                if plausible_company(c):
                    return c

    # platform mail (HackerRank, Criteria…) names the company in the body more reliably than in the subject
    for fn in ((from_body, from_subject) if platform else (from_subject, from_body)):
        c = fn()
        if c:
            return c
    if "workday" in dom:
        c = re.split(r"[.+_-]", local)[0]
        if c and c.lower() not in ("noreply", "no", "donotreply", "myworkday", "workday"):
            return c.upper() if len(c) <= 4 else c.capitalize()
    if name and plausible_company(name):
        return name
    parts = dom.split(".")
    root = parts[-2] if len(parts) >= 2 else dom
    if len(parts) >= 3 and parts[-2] in ("co", "com", "ac", "edu", "org"):
        root = parts[-3]
    return root.upper() if len(root) <= 4 else root.capitalize()


MONTH_PAT = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"


DATE_PATS = [
    ("dmy", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + MONTH_PAT + r",?\s*(\d{4})?", re.I)),
    ("mdy", re.compile(r"\b" + MONTH_PAT + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", re.I)),
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("num", re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")),
]


TIME_PAT = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b(?:\s*\(?([A-Za-z]{1,4})\)?)?|(\d{1,2}):(\d{2})\s*(?:\(?([A-Z]{2,4})\)?)?")


DUE_ANCHORS = [
    r"end login date/?time:?", r"deadline(?: is| to complete)?:?", r"must be completed (?:by|before)",
    r"should be completed (?:by|before)", r"to be completed (?:by|before)", r"completed? (?:it|them|this|the [\w ]{1,30}?)? ?(?:by|before|no later than)",
    r"submit(?:ted)? (?:it |them |this )?(?:by|before|no later than)", r"no later than", r"expires? (?:on|at)",
    r"expiration(?: date)?:?", r"closes? (?:on|at)", r"due (?:on|by|date:?)", r"valid (?:until|through)", r"until",
    r"available (?:until|through)", r"\bby",
]


TZ_FIXED = {"utc": 0, "gmt": 0, "z": 0, "est": -5, "edt": -4, "cst": -6, "cdt": -5, "mst": -7, "mdt": -6,
            "pst": -8, "pdt": -7, "hst": -10, "akst": -9, "akdt": -8, "bst": 1, "cet": 1, "cest": 2, "ist": 5.5,
            "hkt": 8, "sgt": 8, "jst": 9, "kst": 9, "aest": 10, "aedt": 11}


TZ_ZONE = {"et": (-5, -4), "ct": (-6, -5), "mt": (-7, -6), "pt": (-8, -7)}


WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "fourteen": 14}


def _us_dst(d):
    return (3 < d.month < 11) or (d.month == 3 and d.day >= 14) or (d.month == 11 and d.day <= 1)


def _to_local(naive, tz):
    tz = (tz or "").lower()
    off = None
    if tz in TZ_FIXED:
        off = TZ_FIXED[tz]
    elif tz in TZ_ZONE:
        off = TZ_ZONE[tz][1 if _us_dst(naive) else 0]
    if off is None:
        return naive
    aware = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=off)))
    return aware.astimezone().replace(tzinfo=None)


def _parse_date_at(text, ref):
    """Find a date at the start of text -> (date, end_index) or (None, 0)."""
    best = None
    for kind, pat in DATE_PATS:
        m = pat.search(text[:60])
        if not m or m.start() > 25:
            continue
        try:
            if kind == "dmy":
                day, mon, year = int(m.group(1)), MON3.index(m.group(2)[:3].capitalize()) + 1, m.group(3)
            elif kind == "mdy":
                mon, day, year = MON3.index(m.group(1)[:3].capitalize()) + 1, int(m.group(2)), m.group(3)
            elif kind == "iso":
                year, mon, day = m.group(1), int(m.group(2)), int(m.group(3))
            else:
                mon, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
                if year and len(year) == 2:
                    year = "20" + year
            y = int(year) if year else ref.year
            d = dt.date(y, mon, day)
            if not year and d < ref - dt.timedelta(days=45):
                d = dt.date(y + 1, mon, day)
        except (ValueError, IndexError):
            continue
        if best is None or m.start() < best[1]:
            best = (d, m.start(), m.end())
    if best is None:
        return None, 0
    return best[0], best[2]


def _parse_time_at(text):
    m = TIME_PAT.search(text[:40])
    if not m or m.start() > 12:
        return None, None
    if m.group(1):
        h, mi, ap, tz = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower(), m.group(4)
        if ap == "p" and h < 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
    else:
        h, mi, tz = int(m.group(5)), int(m.group(6)), m.group(7)
    if h > 23 or mi > 59:
        return None, None
    return dt.time(h, mi), tz


def extract_due(text, msg_date):
    """-> (local 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' or '', raw text or '')."""
    try:
        ref = to_date(msg_date)
    except (ValueError, TypeError):
        ref = dt.date.today()
    low = text.lower()
    m = re.search(r"within\s+\*?(\d{1,2}|" + "|".join(WORD_NUM) + r")\*?\s+(?:business\s+|calendar\s+)?days", low)
    if not m:
        m = re.search(r"expires?\s+in\s+\*?(\d{1,2}|" + "|".join(WORD_NUM) + r")\*?\s+days?", low)
    rel = None
    if m:
        n = WORD_NUM.get(m.group(1), None)
        n = int(m.group(1)) if n is None else n
        rel = (ref + dt.timedelta(days=n)).isoformat()
    for anchor in DUE_ANCHORS:
        for am in re.finditer(anchor, low):
            window = low[am.end(): am.end() + 70].lstrip(" :*")
            if window.startswith("the ") or window.startswith("your "):
                window = window.split(" ", 1)[1]
            d, end = _parse_date_at(window, ref)
            if d is None:
                continue
            t, tz = _parse_time_at(window[end:].lstrip(" ,at*"))
            raw = text[am.start(): am.end() + 70].split("\n")[0].strip()
            if t is None:
                return d.isoformat(), raw
            local = _to_local(dt.datetime.combine(d, t), tz)
            return local.strftime("%Y-%m-%d %H:%M"), raw
    if rel:
        return rel, m.group(0)
    return "", ""


def extract_minutes(text):
    low = text.lower()
    pats = [r"duration:\s*(\d{1,3})\s*min", r"(\d)\s*hours?\s*(?:and\s*)?(\d{1,2})\s*min",
            r"(\d{1,3})\s*min(?:ute)?s?\s+(?:in total|total)", r"(?:approximately|about|roughly|around|takes?|up to)\s+(\d{1,3})\s*min",
            r"(\d{1,3})[- ]min(?:ute)?s?\s+(?:timed|coding|assessment|test|challenge)", r"(\d{1,3})\s*minutes?\b"]
    for p in pats:
        m = re.search(p, low)
        if not m:
            continue
        if m.re.pattern.startswith(r"(\d)\s*hours?"):
            n = int(m.group(1)) * 60 + int(m.group(2))
        else:
            n = int(m.group(1))
        if 5 <= n <= 600:
            return n
    m = re.search(r"(?:approximately|about|roughly|up to|takes?)\s+(\d)\s*hours?", low)
    if m:
        return int(m.group(1)) * 60
    return None


GMAIL_TERMS = ["interview", "assessment", "hackerrank", "codesignal", "codility", "hirevue", '"coding challenge"',
               '"online test"', '"take-home"', "unfortunately", '"moving forward"', '"other candidates"',
               '"not selected"', "regret", '"offer letter"', '"next steps"', '"next step"', '"action required"',
               "deadline", '"phone screen"', "recruiter", "recruiting", '"your application"',
               '"thank you for applying"', "expires", "candidacy", "interest", "application", "applying", "applied",
               "submission"]


def imap_quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decode_hdr(raw):
    from email.header import decode_header, make_header
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def clean_subject(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"^(?:(?:re|fwd?|fw|aw)\s*:\s*)+", "", s, flags=re.I)
    return s


def msg_date(raw):
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(raw)
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return d.date().isoformat()
    except Exception:
        return today_str()


def html_to_text(h):
    h = re.sub(r"(?is)<(style|script|head).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return htmlmod.unescape(h)


def body_text(msg):
    plain = html = None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, "replace")
        except LookupError:
            text = payload.decode("utf-8", "replace")
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and html is None:
            html = text
    text = plain if plain and plain.strip() else (html_to_text(html) if html else "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def imap_search(M, days, since=None):
    when = ("after:%s" % since.replace("-", "/")) if since else ("newer_than:%dd" % days)
    q = "%s (%s)" % (when, " OR ".join(GMAIL_TERMS))
    try:
        typ, data = M.uid("SEARCH", "X-AV-RAW", imap_quote(q))
        if typ == "OK" and data and data[0] is not None:
            return data[0].split()
    except Exception:
        pass
    start = to_date(since) if since else (dt.date.today() - dt.timedelta(days=days))
    typ, data = M.uid("SEARCH", "SINCE", "%d-%s-%d" % (start.day, MON3[start.month - 1], start.year))
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def fetch_headers(M, uids):
    import email
    from email.utils import parseaddr
    out = []
    for chunk in _chunks(uids, 100):
        typ, data = M.uid("FETCH", b",".join(chunk),
                          "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE)])")
        if typ != "OK":
            continue
        for part in data:
            if not isinstance(part, tuple) or len(part) < 2:
                continue
            m = re.search(rb"UID (\d+)", part[0])
            if not m:
                continue
            msg = email.message_from_bytes(part[1])
            name, addr = parseaddr(decode_hdr(msg.get("From", "")))
            out.append({"uid": m.group(1), "subject": clean_subject(decode_hdr(msg.get("Subject", ""))),
                        "from_name": name.strip(), "from_addr": addr.lower(), "date": msg_date(msg.get("Date")),
                        "msgid": (msg.get("Message-ID") or "").strip(), "unsub": bool(msg.get("List-Unsubscribe"))})
    return out


def fetch_bodies(M, uids, progress=None):
    import email
    out = {}
    for n, chunk in enumerate(_chunks(uids, 25)):
        if progress and n and n % 8 == 0:
            progress(n * 25, len(uids))
        typ, data = M.uid("FETCH", b",".join(chunk), "(BODY.PEEK[]<0.12000>)")
        if typ != "OK":
            continue
        for part in data:
            if not isinstance(part, tuple) or len(part) < 2:
                continue
            m = re.search(rb"UID (\d+)", part[0])
            if not m:
                continue
            try:
                out[m.group(1)] = body_text(email.message_from_bytes(part[1]))
            except Exception:
                out[m.group(1)] = ""
    return out


def make_item(acct, hd, body, kind, platform):
    company = company_from(hd, body, platform)
    due, due_text = "", ""
    mins = None
    if kind in ("oa", "interview", "important"):
        due, due_text = extract_due(hd["subject"] + "\n" + body, hd["date"])
        mins = extract_minutes(body) if kind == "oa" else None
    if due and due < hd["date"]:
        due, due_text = "", ""
    parts = []
    if mins:
        parts.append(fmt_minutes(mins))
    if due:
        parts.append("closes " + fmt_when(due))
    elif due_text:
        parts.append(clip(due_text, 40))
    msgid = hd["msgid"].strip("<>")
    link = ""
    if msgid:
        link = "https://mail.google.com/mail/?authuser=%s#search/rfc822msgid:%s" % (acct, urllib.parse.quote(msgid, safe=""))
    return {"id": new_id(), "key": acct + ":" + (msgid or hd["uid"].decode()), "account": acct, "date": hd["date"],
            "company": company, "kind": kind, "platform": platform, "title": clip(hd["subject"], 110),
            "role": _tracker().extract_role(hd["subject"], body, company) if kind in _tracker().EVENT_KINDS else "",
            "detail": " · ".join(parts), "due": due, "due_text": due_text, "mins": mins,
            "status": "done" if kind == "oa_done" else "open", "link": link}


def _tracker():
    import tracker
    return tracker


def msg_key(acct, hd):
    return acct + ":" + (hd["msgid"].strip("<>") or hd["uid"].decode())


def scan_account(acct, pw, cfg, days, progress, since=None, known=(), read=None):
    """since='YYYY-MM-DD' reads everything from that date (history); known = message keys already processed,
    which are skipped so a scan only reads new mail."""
    import imaplib
    host = (cfg.get("hosts") or {}).get(acct, "imap.gmail.com")
    M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=90)
    try:
        M.login(acct, pw)
        typ, _ = M.select('"[Gmail]/All Mail"', readonly=True)
        if typ != "OK":
            M.select("INBOX", readonly=True)
        uids = imap_search(M, days, since)
        if not since:
            uids = uids[-1500:]
        progress("%s: %d messages match, reading headers…" % (acct, len(uids)))
        heads = fetch_headers(M, uids)
        own = {a.lower() for a in cfg.get("accounts", [])}
        cands = [h for h in heads if h["from_addr"] not in own
                 and not any(d in h["from_addr"].split("@")[-1] for d in SKIP_DOMAINS)
                 and msg_key(acct, h) not in known]
        if not since:
            cands = cands[-600:]
        progress("%s: reading %d new message%s…" % (acct, len(cands), "" if len(cands) == 1 else "s"))
        bodies = fetch_bodies(M, [c["uid"] for c in cands],
                              progress=lambda i, n: progress("%s: reading %d of %d…" % (acct, i, n)))
        items = []
        for hd in cands:
            if read is not None and hd["uid"] in bodies:
                read[msg_key(acct, hd)] = hd["date"]
            body = bodies.get(hd["uid"], "")
            kind, platform = classify(hd, body)
            if not kind:
                continue
            items.append(make_item(acct, hd, body, kind, platform))
        return items
    finally:
        try:
            M.logout()
        except Exception:
            pass


def scan_all(cfg, progress=lambda t: None, history=False):
    """Scan every account. An account's first scan (or history=True) reads everything since history_since so
    the application tracker starts complete; later scans read only mail they haven't seen."""
    TR = _tracker()
    accounts = cfg.get("accounts") or []
    days = int(cfg.get("scan_days") or 30)
    since = cfg.get("history_since") or TR.DEFAULT_HISTORY_SINCE
    inbox = load_inbox()
    tr = TR.load(APP_DIR)
    TR.ingest_items(tr, inbox["items"])          # the tracker also knows whatever the inbox already has
    tr.setdefault("read", {})                    # every message already looked at, relevant or not
    keep = (dt.date.today() - dt.timedelta(days=max(days, 45) + 15)).isoformat()
    tr["read"] = {k: v for k, v in tr["read"].items() if v >= keep}
    known = {i.get("key") for i in inbox["items"]} | set(tr["seen"])
    found, errors, counts, full_done = [], {}, {}, []
    for acct in accounts:
        progress("Connecting to %s…" % acct)
        pw = password_get(acct, cfg)
        if not pw:
            errors[acct] = "no password saved — run: today mail add %s" % acct
            continue
        full = history or acct not in tr["history"]
        if full:
            progress("%s: reading your mail since %s (first time only, a few minutes)…" % (acct, short_date(since)))
        try:
            items = scan_account(acct, pw, cfg, days, progress, since=since if full else None,
                                 known=set(tr["seen"]) if history else known | set(tr["read"]),
                                 read=tr["read"])
        except Exception as e:  # imaplib errors, ssl, socket
            errors[acct] = re.sub(r"\s+", " ", str(e))[:160] or e.__class__.__name__
            continue
        found.extend(items)
        counts[acct] = len(items)
        if full:
            full_done.append(acct)
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    merge_inbox(inbox, [f for f in found if f.get("date", "") >= cutoff], accounts, errors, scanned_ok=set(counts))
    save_inbox(inbox)
    read = tr["read"]
    tr = TR.load(APP_DIR)            # fresh copy: keeps any edits made (in the window) while the scan ran
    tr.setdefault("read", {}).update(read)
    TR.ingest_items(tr, inbox["items"])
    TR.ingest_items(tr, found)
    if full_done:
        TR.drop_seed_events(tr, full_done)
        for a in full_done:
            tr["history"][a] = {"since": since, "done": now().strftime("%Y-%m-%d %H:%M")}
    TR.save(APP_DIR, tr)
    return {"found": len(found), "counts": counts, "errors": errors, "inbox": inbox, "tracker": TR.totals(tr)}
