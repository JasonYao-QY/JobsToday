#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""today — a one-day scheduler for the terminal.

  today                     open the interactive view
  today show                print today's plan
  today add [TIME] TITLE [--min N] [--note TEXT] [--urgent]
  today done N | undo N | rm N | move N TIME | edit N TITLE | note N TEXT
  today carry               bring unfinished items over from the previous plan
  today tomorrow ...        run a plan command against tomorrow (today tomorrow add 7pm "StoneRidge OA")
  today daily               recurring daily tasks
  today daily add TITLE [--count N] | rm N | done N | undo N | inc N [K] | set N K
  today deadline            deadlines (manual ones + open assessments found in mail)
  today deadline add NAME WHEN | rm N | done N
  today mail add EMAIL | rm EMAIL | list      accounts for the mail scan (app password -> Keychain)
  today scan [--history]    scan mail for assessments, interviews, rejections, important mail
                            (--history re-reads everything since history_since, default 2026-06-01)
  today inbox [--all]       what the last scan found
  today apps [--all]        application tracker: totals, and where each application stands
  today help

TIME looks like 6:10pm, 18:10, 6pm.  WHEN looks like "2026-10-01 11:27", "Oct 1 11:27am", "tomorrow 9pm".
Data lives in ~/.config/today (override with TODAY_HOME).
"""

import datetime as dt
import html as htmlmod
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import uuid

APP_DIR = os.environ.get("TODAY_HOME") or os.path.join(os.path.expanduser("~"), ".config", "today")
PLANS_DIR = os.path.join(APP_DIR, "plans")
DAILY_FILE = os.path.join(APP_DIR, "daily.json")
DEADLINES_FILE = os.path.join(APP_DIR, "deadlines.json")
INBOX_FILE = os.path.join(APP_DIR, "inbox.json")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
KEYCHAIN_SERVICE = "today-mail"
VERSION = "1.1.0"

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MON3 = [m[:3] for m in MONTHS]


# ----------------------------------------------------------------------------
# small utilities
# ----------------------------------------------------------------------------

def now():
    return dt.datetime.now()


def today_str():
    return dt.date.today().isoformat()


def to_date(s):
    return dt.date.fromisoformat(s)


def human_date(date_str):
    d = to_date(date_str)
    return "%s, %s %d" % (DOW[d.weekday()], MONTHS[d.month - 1], d.day)


def short_date(date_str):
    try:
        d = to_date(date_str[:10])
    except ValueError:
        return date_str
    return "%s %d" % (MON3[d.month - 1], d.day)


def new_id():
    return uuid.uuid4().hex[:8]


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def clip(s, n):
    s = str(s)
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


# ----------------------------------------------------------------------------
# times and dates
# ----------------------------------------------------------------------------

TIME_RE = re.compile(r"^\s*(\d{1,2})(?::?(\d{2}))?\s*([ap])?\.?m?\.?\s*$", re.I)


def parse_time(s):
    """'5:30 PM', '17:30', '5pm', '530pm', '5' -> 'HH:MM' or None.
    A bare hour 1-6 with no am/pm means afternoon/evening (this is a day planner)."""
    m = TIME_RE.match(s or "")
    if not m:
        return None
    h = int(m.group(1))
    mnt = int(m.group(2) or 0)
    ap = (m.group(3) or "").lower()
    if mnt > 59 or h > 24:
        return None
    if ap == "p":
        if h < 12:
            h += 12
    elif ap == "a":
        if h == 12:
            h = 0
    elif 1 <= h <= 6:
        h += 12
    if h == 24:
        h = 0
    if h > 23:
        return None
    return "%02d:%02d" % (h, mnt)


def split_time_prefix(tokens):
    """Return (hhmm or '', remaining tokens).  Accepts '5:30pm', '17:30', '5pm', '5:30 pm', '5 pm'."""
    if not tokens:
        return "", tokens
    t0 = tokens[0]
    if len(tokens) >= 2 and re.fullmatch(r"[ap]\.?m\.?", tokens[1], re.I):
        hhmm = parse_time(t0 + tokens[1])
        if hhmm:
            return hhmm, tokens[2:]
    if ":" in t0 or re.search(r"[ap]\.?m\.?$", t0, re.I):
        hhmm = parse_time(t0)
        if hhmm:
            return hhmm, tokens[1:]
    return "", tokens


def fmt_time(hhmm):
    if not hhmm:
        return ""
    h, m = [int(x) for x in hhmm.split(":")]
    ap = "AM" if h < 12 else "PM"
    return "%d:%02d %s" % (h % 12 or 12, m, ap)


def add_minutes(hhmm, mins):
    h, m = [int(x) for x in hhmm.split(":")]
    total = (h * 60 + m + mins) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


WHEN_FORMATS = [
    "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I %p", "%Y-%m-%d",
    "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%m/%d %H:%M", "%m/%d %I:%M %p", "%m/%d %I %p", "%m/%d",
    "%b %d %Y %H:%M", "%b %d %Y %I:%M %p", "%b %d %Y", "%B %d %Y %H:%M", "%B %d %Y %I:%M %p", "%B %d %Y",
    "%b %d %H:%M", "%b %d %I:%M %p", "%b %d %I %p", "%b %d", "%B %d %H:%M", "%B %d %I:%M %p", "%B %d %I %p", "%B %d",
    "%d %b %Y %H:%M", "%d %b %Y %I:%M %p", "%d %b %Y", "%d %B %Y %I:%M %p", "%d %B %Y",
]


def parse_when(text):
    """'2026-10-01 11:27', 'Oct 1 11:27am', 'tomorrow 9pm', 'today', '10/1' -> 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'."""
    s = " ".join((text or "").replace(",", " ").split())
    if not s:
        return None
    s = re.sub(r"(\d)\s*([ap])\.?m\.?(?=\s|$)", lambda m: m.group(1) + " " + m.group(2).upper() + "M", s, flags=re.I)
    low = s.lower()
    for word, delta in (("today", 0), ("tonight", 0), ("tomorrow", 1)):
        if low.startswith(word):
            base = dt.date.today() + dt.timedelta(days=delta)
            rest = s[len(word):].strip()
            if not rest:
                return base.isoformat()
            hhmm = parse_time(rest)
            return "%s %s" % (base.isoformat(), hhmm) if hhmm else None
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    for fmt in WHEN_FORMATS:
        try:
            d = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = d.replace(year=dt.date.today().year)
            if d.date() < dt.date.today() - dt.timedelta(days=60):
                d = d.replace(year=d.year + 1)
        has_time = "%H" in fmt or "%I" in fmt
        return d.strftime("%Y-%m-%d %H:%M" if has_time else "%Y-%m-%d")
    return None


def when_dt(at):
    if not at:
        return None
    try:
        if len(at) == 10:
            return dt.datetime.combine(to_date(at), dt.time(23, 59))
        return dt.datetime.strptime(at, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def fmt_when(at):
    d = when_dt(at)
    if d is None:
        return at or ""
    today = dt.date.today()
    day = d.date()
    if day == today:
        label = "today"
    elif day == today + dt.timedelta(days=1):
        label = "tomorrow"
    elif day == today - dt.timedelta(days=1):
        label = "yesterday"
    else:
        label = "%s %s %d" % (DOW[day.weekday()][:3], MON3[day.month - 1], day.day)
    if len(at) == 10:
        return label
    return "%s %s" % (label, fmt_time(d.strftime("%H:%M")))


def _span(secs):
    secs = int(secs)
    days, rem = divmod(secs, 86400)
    hrs, rem = divmod(rem, 3600)
    mins = rem // 60
    if days >= 3:
        return "%dd" % days
    if days >= 1:
        return "%dd %dh" % (days, hrs)
    if hrs >= 1:
        return "%dh %02dm" % (hrs, mins)
    return "%dm" % mins


def until(at):
    """-> (text, severity) with severity in soon / near / ok / over / none."""
    d = when_dt(at)
    if d is None:
        return "rolling", "none"
    secs = (d - now()).total_seconds()
    if secs < 0:
        return "passed %s ago" % _span(-secs), "over"
    return "in " + _span(secs), ("soon" if secs < 86400 else "near" if secs < 3 * 86400 else "ok")


# ----------------------------------------------------------------------------
# plan (one file per day)
# ----------------------------------------------------------------------------

def plan_path(date_str):
    return os.path.join(PLANS_DIR, date_str + ".json")


def load_plan(date_str):
    plan = load_json(plan_path(date_str), None) or {}
    plan["date"] = date_str
    plan.setdefault("items", [])
    return plan


def save_plan(plan):
    save_json(plan_path(plan["date"]), plan)


def sorted_items(items):
    return sorted(items, key=lambda i: (not i.get("time"), i.get("time") or "", i.get("created", 0)))


def add_item(plan, title, time_hhmm="", note="", mins=None, urgent=False):
    it = {"id": new_id(), "title": title, "time": time_hhmm or "", "note": note or "",
          "mins": mins, "urgent": bool(urgent), "done": False, "created": time.time()}
    plan["items"].append(it)
    return it


def plan_dates():
    try:
        names = os.listdir(PLANS_DIR)
    except OSError:
        return []
    return sorted(n[:-5] for n in names if n.endswith(".json") and len(n) == 15)


def carry_candidates(date_str):
    """Unfinished items from the most recent plan before date_str -> (items, from_date)."""
    for d in reversed(plan_dates()):
        if d >= date_str:
            continue
        items = [i for i in load_plan(d)["items"] if not i.get("done")]
        return items, d
    return [], None


def carry_over(plan):
    items, src = carry_candidates(plan["date"])
    have = {i["title"].strip().lower() for i in plan["items"]}
    n = 0
    for i in items:
        if i["title"].strip().lower() in have:
            continue
        add_item(plan, i["title"], i.get("time", ""), i.get("note", ""), i.get("mins"), i.get("urgent"))
        n += 1
    return n, src


# ----------------------------------------------------------------------------
# daily (recurring) tasks
# ----------------------------------------------------------------------------

def load_daily():
    d = load_json(DAILY_FILE, {}) or {}
    d.setdefault("items", [])
    d.setdefault("log", {})
    return d


def save_daily(d):
    keep = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    d["log"] = {k: v for k, v in d["log"].items() if k >= keep}
    save_json(DAILY_FILE, d)


def guess_target(title):
    m = re.search(r"\b(\d{1,4})\b", title)
    if m and int(m.group(1)) >= 2:
        return int(m.group(1))
    return None


def daily_state(d, item, date_str):
    st = d["log"].get(date_str, {}).get(item["id"], {})
    count = int(st.get("count", 0))
    tgt = item.get("target")
    done = bool(st.get("done")) if tgt is None else count >= tgt
    return done, count


def daily_set(d, item, date_str, done=None, count=None):
    st = d["log"].setdefault(date_str, {}).setdefault(item["id"], {"done": False, "count": 0})
    tgt = item.get("target")
    if count is not None:
        st["count"] = max(0, int(count))
        if tgt is not None:
            st["count"] = min(st["count"], tgt)
    if done is not None:
        if tgt is None:
            st["done"] = bool(done)
        else:
            st["count"] = tgt if done else 0


# ----------------------------------------------------------------------------
# deadlines (manual) + merge with mail
# ----------------------------------------------------------------------------

def load_deadlines():
    d = load_json(DEADLINES_FILE, {}) or {}
    d.setdefault("items", [])
    return d


def save_deadlines(d):
    save_json(DEADLINES_FILE, d)


def all_deadlines(deadlines, inbox):
    out = []
    for d in deadlines["items"]:
        out.append({"id": d["id"], "name": d["name"], "at": d.get("at", ""), "note": d.get("note", ""),
                    "done": bool(d.get("done")), "source": "manual"})
    recent = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    for i in inbox["items"]:
        if i.get("kind") != "oa" or i.get("status") == "hidden":
            continue
        done = i.get("status") == "done"
        if done and i.get("date", "") < recent:
            continue
        name = "%s %s" % (i.get("company") or "?", i.get("platform") or "OA")
        out.append({"id": i["id"], "name": name, "at": i.get("due", ""), "note": i.get("detail", ""),
                    "done": done, "source": "inbox", "date": i.get("date", "")})
    for d in out:
        if d["done"]:
            d["until"], d["sev"] = "done", "done"
        else:
            d["until"], d["sev"] = until(d["at"])
    out.sort(key=lambda d: (2, "", "") if d["done"] else (0 if d["at"] else 1, d["at"] or "", d["name"]))
    return out


# ----------------------------------------------------------------------------
# inbox (what the mail scan found)
# ----------------------------------------------------------------------------

KIND_LABEL = {"offer": "OFFER", "interview": "INTVW", "oa": "OA", "oa_done": "OA ✓",
              "important": "NOTE", "rejection": "REJ", "applied": "APPLD"}


def load_inbox():
    d = load_json(INBOX_FILE, {}) or {}
    d.setdefault("items", [])
    d.setdefault("updated", "")
    d.setdefault("accounts", [])
    d.setdefault("errors", {})
    return d


def save_inbox(d):
    save_json(INBOX_FILE, d)


def _inbox_group(i):
    k, st = i.get("kind"), i.get("status")
    if k == "offer":
        return 0
    if k == "interview":
        return 1
    if k == "oa":
        return 4 if st == "done" else (2 if i.get("due") else 3)
    if k == "important":
        return 5
    if k == "rejection":
        return 6
    return 7


def inbox_visible(inbox, days=30, include_hidden=False):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    items = [i for i in inbox["items"]
             if i.get("date", "") >= cutoff and i.get("kind") != "applied"
             and (include_hidden or i.get("status") != "hidden")]
    items.sort(key=lambda i: i.get("date", ""), reverse=True)
    items.sort(key=lambda i: (_inbox_group(i), i.get("due") or "9999-99-99" if _inbox_group(i) == 2 else ""))
    return items


def inbox_counts(inbox, days=30):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    c = {"offer": 0, "interview": 0, "oa_open": 0, "oa_done": 0, "important": 0, "rejection": 0, "applied": 0}
    for i in inbox["items"]:
        if i.get("date", "") < cutoff or i.get("status") == "hidden":
            continue
        k = i.get("kind")
        if k == "oa":
            c["oa_done" if i.get("status") == "done" else "oa_open"] += 1
        elif k in c:
            c[k] += 1
    return c


def inbox_summary(inbox):
    c = inbox_counts(inbox)
    parts = []
    if c["offer"]:
        parts.append(plural(c["offer"], "offer"))
    parts.append("%d OA open" % c["oa_open"])
    parts.append(plural(c["interview"], "interview"))
    parts.append(plural(c["rejection"], "rejection"))
    if c["important"]:
        parts.append("%d flagged" % c["important"])
    if c["applied"]:
        parts.append("%d applied" % c["applied"])
    return " · ".join(parts) + " (30d)"


# ----------------------------------------------------------------------------
# config + keychain
# ----------------------------------------------------------------------------

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


# ----------------------------------------------------------------------------
# mail scan: classification
# ----------------------------------------------------------------------------

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


# ---- company name -----------------------------------------------------------

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


# ---- deadline + duration extraction ------------------------------------------

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


def fmt_minutes(n):
    if not n:
        return ""
    if n >= 60 and n % 60 == 0:
        return "%dh" % (n // 60)
    if n > 90:
        return "%dh %02dm" % (n // 60, n % 60)
    return "%dm" % n


# ---- IMAP -------------------------------------------------------------------

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


def merge_inbox(inbox, found, accounts, errors, scanned_ok=()):
    # items seeded by hand for an account are superseded by the first successful scan of that account
    old = [i for i in inbox["items"]
           if not (i.get("source") == "seed" and i.get("account") in scanned_ok)]
    by_key = {i["key"]: i for i in old if i.get("key")}
    for f in found:
        old = by_key.get(f["key"])
        if old:
            f["id"] = old["id"]
            f["status"] = old.get("status", f["status"])
            if old.get("kind") == "oa" and f["kind"] == "oa" and old.get("status") == "done":
                f["status"] = "done"
        by_key[f["key"]] = f
    items = list(by_key.values())
    # an OA is done once the platform confirmed a submission for that company
    done_cos = {(i.get("company") or "").lower() for i in items if i.get("kind") == "oa_done"}
    for i in items:
        if i.get("kind") == "oa" and i.get("status") == "open" and (i.get("company") or "").lower() in done_cos:
            i["status"] = "done"
    # collapse duplicate open OAs for one company within a few days (invite + platform mail)
    items.sort(key=lambda i: (i.get("date", ""), 1 if i.get("due") else 0, 1 if i.get("platform") else 0))
    seen = {}
    keep = []
    for i in reversed(items):
        if i.get("kind") == "oa" and i.get("status") != "hidden":
            k = (i.get("company") or "").lower()
            prev = seen.get(k)
            if prev is not None and abs((to_date(prev["date"]) - to_date(i["date"])).days) <= 3:
                if not prev.get("due") and i.get("due"):
                    prev["due"], prev["detail"], prev["mins"] = i["due"], i["detail"], i.get("mins")
                if not prev.get("platform") and i.get("platform"):
                    prev["platform"] = i["platform"]
                continue
            seen[k] = i
        keep.append(i)
    cutoff = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    keep = [i for i in keep if i.get("date", "") >= cutoff]
    keep.sort(key=lambda i: i.get("date", ""), reverse=True)
    inbox["items"] = keep
    inbox["updated"] = now().strftime("%Y-%m-%d %H:%M")
    inbox["accounts"] = list(accounts)
    inbox["errors"] = dict(errors)


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


# ----------------------------------------------------------------------------
# plain-text output
# ----------------------------------------------------------------------------

def deadline_when(d):
    if d["done"]:
        return ("submitted " + short_date(d["date"])) if d.get("date") else "done"
    return fmt_when(d["at"]) if d["at"] else "rolling"


def bar_text(done, total, width=10):
    if total <= 0:
        return "▯" * width
    f = int(round(width * min(done, total) / float(total)))
    return "▮" * f + "▯" * (width - f)


def print_plan(plan, daily, deadlines, inbox):
    items = sorted_items(plan["items"])
    done = sum(1 for i in items if i.get("done"))
    print("TODAY  %s%s" % (human_date(plan["date"]), "" if plan["date"] == today_str() else "  (%s)" % plan["date"]))
    if items:
        print("       %d/%d done  %s" % (done, len(items), bar_text(done, len(items))))
    dls = all_deadlines(deadlines, inbox)
    if dls:
        print("\nDEADLINES")
        for d in dls:
            mark = "✓" if d["done"] else ("●" if d["sev"] in ("soon", "over") else "○")
            when = deadline_when(d)
            print("  %s %-28s %-22s %s" % (mark, clip(d["name"], 28), when, "" if d["done"] else d["until"]))
    print("\nSCHEDULE")
    if not items:
        print("  nothing planned — today add 6pm \"Something\"")
    for n, it in enumerate(items, 1):
        t = fmt_time(it.get("time")).rjust(8) if it.get("time") else "       –"
        flags = ""
        if it.get("mins"):
            flags += " · " + fmt_minutes(it["mins"])
        if it.get("urgent"):
            flags += " ⚠"
        print("  %2d. %s %s %s%s" % (n, t, "✓" if it.get("done") else " ", it["title"], flags))
        if it.get("note"):
            print("                   └ %s" % it["note"])
    print("\nDAILY")
    if not daily["items"]:
        print("  none — today daily add \"Finish one leetcode problem\"")
    for n, it in enumerate(daily["items"], 1):
        dn, cnt = daily_state(daily, it, plan["date"])
        if it.get("target"):
            print("  %2d. %s %2d/%-3d %s" % (n, bar_text(cnt, it["target"]), cnt, it["target"], it["title"]))
        else:
            print("  %2d. [%s] %s" % (n, "✓" if dn else " ", it["title"]))
    print("\nINBOX  %s" % (("scanned " + inbox["updated"]) if inbox["updated"] else "never scanned"))
    print("  " + inbox_summary(inbox))
    for i in inbox_visible(inbox)[:8]:
        print_inbox_item(i)


def print_inbox_item(i, n=None):
    label = KIND_LABEL.get(i.get("kind"), "?")
    if i.get("kind") == "oa" and i.get("status") == "done":
        label = "OA ✓"
    right = ("closes %s (%s)" % (fmt_when(i["due"]), until(i["due"])[0])) if i.get("due") and i.get("status") == "open" else short_date(i.get("date", ""))
    prefix = ("%2d. " % n) if n is not None else "  "
    hidden = "  (hidden)" if i.get("status") == "hidden" else ""
    print("%s%-5s %-16s %-48s %s%s" % (prefix, label, clip(i.get("company") or "", 16), clip(i.get("title", ""), 48), right, hidden))
    if i.get("detail") and i.get("kind") == "oa":
        print("%s      %s" % (" " * len(prefix), i["detail"]))


def print_inbox(inbox, show_all=False):
    print("INBOX  %s  ·  %s" % (("scanned " + inbox["updated"]) if inbox["updated"] else "never scanned", inbox_summary(inbox)))
    if inbox.get("errors"):
        for a, e in inbox["errors"].items():
            print("  ! %s: %s" % (a, e))
    items = inbox_visible(inbox, include_hidden=show_all)
    if not items:
        print("  nothing yet — today mail add you@gmail.com, then: today scan")
    for n, i in enumerate(items, 1):
        print_inbox_item(i, n)
    if show_all:
        applied = [i for i in inbox["items"] if i.get("kind") == "applied"]
        if applied:
            print("\n  application confirmations (%d):" % len(applied))
            for i in applied:
                print("    %s  %-18s %s" % (short_date(i["date"]), clip(i.get("company") or "", 18), clip(i["title"], 60)))


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _pick(items, arg, what):
    try:
        n = int(arg)
        return items[n - 1]
    except (ValueError, IndexError):
        sys.exit("no %s #%s (run `today show` for numbers)" % (what, arg))


def _flag(args, name, default=None, has_value=True):
    if name in args:
        i = args.index(name)
        if has_value:
            val = args[i + 1] if i + 1 < len(args) else default
            del args[i:i + 2]
            return val
        del args[i]
        return True
    return default


def cli_plan(args, date_str):
    plan = load_plan(date_str)
    cmd = args[0] if args else "show"
    rest = args[1:]
    items = sorted_items(plan["items"])
    if cmd in ("show", "ls", "list"):
        print_plan(plan, load_daily(), load_deadlines(), load_inbox())
        return
    if cmd == "add":
        mins = _flag(rest, "--min")
        note = _flag(rest, "--note", "")
        urgent = _flag(rest, "--urgent", False, has_value=False)
        hhmm, rest = split_time_prefix(rest)
        title = " ".join(rest).strip()
        if not title:
            sys.exit("usage: today add [TIME] TITLE [--min N] [--note TEXT] [--urgent]")
        it = add_item(plan, title, hhmm, note, int(mins) if mins else None, urgent)
        save_plan(plan)
        print("added  %s %s" % (fmt_time(it["time"]) or "(no time)", it["title"]))
        return
    if cmd in ("done", "undo", "rm", "remove", "move", "edit", "note", "urgent"):
        if not rest:
            sys.exit("usage: today %s N ..." % cmd)
        it = _pick(items, rest[0], "item")
        if cmd == "done":
            it["done"] = True
        elif cmd == "undo":
            it["done"] = False
        elif cmd in ("rm", "remove"):
            plan["items"] = [i for i in plan["items"] if i["id"] != it["id"]]
        elif cmd == "move":
            hhmm = parse_time(" ".join(rest[1:]))
            if not hhmm:
                sys.exit("time not understood: %r" % " ".join(rest[1:]))
            it["time"] = hhmm
        elif cmd == "edit":
            it["title"] = " ".join(rest[1:]).strip() or it["title"]
        elif cmd == "note":
            it["note"] = " ".join(rest[1:]).strip()
        elif cmd == "urgent":
            it["urgent"] = not it.get("urgent")
        save_plan(plan)
        print("%s  %s" % (cmd, it["title"]))
        return
    if cmd == "carry":
        n, src = carry_over(plan)
        save_plan(plan)
        print("carried %d item%s from %s" % (n, "" if n == 1 else "s", short_date(src) if src else "nowhere"))
        return
    sys.exit("unknown command %r — try: today help" % cmd)


def cli_daily(args, date_str):
    d = load_daily()
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd in ("list", "ls", "show"):
        for n, it in enumerate(d["items"], 1):
            dn, cnt = daily_state(d, it, date_str)
            if it.get("target"):
                print("%2d. %s %2d/%-3d %s" % (n, bar_text(cnt, it["target"]), cnt, it["target"], it["title"]))
            else:
                print("%2d. [%s] %s" % (n, "✓" if dn else " ", it["title"]))
        if not d["items"]:
            print("no daily tasks — today daily add \"Finish one leetcode problem\"")
        return
    if cmd == "add":
        count = _flag(rest, "--count")
        title = " ".join(rest).strip()
        if not title:
            sys.exit("usage: today daily add TITLE [--count N]")
        target = int(count) if count else guess_target(title)
        d["items"].append({"id": new_id(), "title": title, "target": target})
        save_daily(d)
        print("added  %s%s" % (title, ("  (counter to %d)" % target) if target else ""))
        return
    if not rest:
        sys.exit("usage: today daily %s N" % cmd)
    it = _pick(d["items"], rest[0], "daily task")
    if cmd in ("rm", "remove"):
        d["items"] = [i for i in d["items"] if i["id"] != it["id"]]
    elif cmd == "done":
        daily_set(d, it, date_str, done=True)
    elif cmd == "undo":
        daily_set(d, it, date_str, done=False)
    elif cmd in ("inc", "+"):
        k = int(rest[1]) if len(rest) > 1 else 1
        daily_set(d, it, date_str, count=daily_state(d, it, date_str)[1] + k)
    elif cmd == "set":
        daily_set(d, it, date_str, count=int(rest[1]))
    elif cmd == "edit":
        it["title"] = " ".join(rest[1:]).strip() or it["title"]
    elif cmd == "count":
        it["target"] = int(rest[1]) if len(rest) > 1 and rest[1] != "off" else None
    else:
        sys.exit("unknown daily command %r" % cmd)
    save_daily(d)
    dn, cnt = daily_state(d, it, date_str)
    print("%s  %s%s" % (cmd, it["title"], ("  %d/%d" % (cnt, it["target"])) if it.get("target") else ("  ✓" if dn else "")))


def cli_deadline(args):
    d = load_deadlines()
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd in ("list", "ls", "show"):
        for n, x in enumerate(all_deadlines(d, load_inbox()), 1):
            mark = "✓" if x["done"] else ("●" if x["sev"] in ("soon", "over") else "○")
            src = "" if x["source"] == "manual" else "  (from mail)"
            print("%2d. %s %-28s %-22s %s%s" % (n, mark, clip(x["name"], 28), deadline_when(x),
                                                 "" if x["done"] else x["until"], src))
        return
    if cmd == "add":
        if not rest:
            sys.exit('usage: today deadline add NAME WHEN   e.g. today deadline add "Akuna OA" "2026-10-01 11:27"')
        name = rest[0]
        when = parse_when(" ".join(rest[1:])) if len(rest) > 1 else ""
        if len(rest) > 1 and not when:
            sys.exit("time not understood: %r  (try: 2026-10-01 11:27, Oct 1 11:27am, tomorrow 9pm)" % " ".join(rest[1:]))
        d["items"].append({"id": new_id(), "name": name, "at": when or "", "note": "", "done": False})
        save_deadlines(d)
        print("added  %s  %s" % (name, fmt_when(when) if when else "(no date)"))
        return
    merged = all_deadlines(d, load_inbox())
    x = _pick(merged, rest[0] if rest else "?", "deadline")
    if x["source"] == "inbox":
        inbox = load_inbox()
        for i in inbox["items"]:
            if i["id"] == x["id"]:
                if cmd == "done":
                    i["status"] = "done"
                elif cmd in ("rm", "remove"):
                    i["status"] = "hidden"
                elif cmd == "undo":
                    i["status"] = "open"
        save_inbox(inbox)
    else:
        for i in d["items"]:
            if i["id"] == x["id"]:
                if cmd == "done":
                    i["done"] = True
                elif cmd == "undo":
                    i["done"] = False
        if cmd in ("rm", "remove"):
            d["items"] = [i for i in d["items"] if i["id"] != x["id"]]
        save_deadlines(d)
    print("%s  %s" % (cmd, x["name"]))


def cli_mail(args):
    cfg = load_config()
    cmd = args[0] if args else "list"
    if cmd in ("list", "ls"):
        if not cfg["accounts"]:
            print("no accounts — today mail add you@gmail.com")
        for a in cfg["accounts"]:
            print("  %s  (%s)" % (a, "password saved" if password_get(a, cfg) else "NO PASSWORD"))
        return
    if cmd == "add":
        if len(args) < 2 or "@" not in args[1]:
            sys.exit("usage: today mail add you@gmail.com")
        acct = args[1].strip().lower()
        import getpass
        print("Google account: %s" % acct)
        print("Create an app password at https://myaccount.google.com/apppasswords (needs 2-Step Verification),")
        print("then paste it here. Spaces are fine. It is stored in the macOS Keychain (service: %s)." % KEYCHAIN_SERVICE)
        pw = getpass.getpass("App password: ").replace(" ", "").strip()
        if not pw:
            sys.exit("nothing entered")
        print("Testing IMAP login…", end=" ", flush=True)
        import imaplib
        host = cfg["hosts"].get(acct, "imap.gmail.com")
        try:
            M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=30)
            M.login(acct, pw)
            M.logout()
        except Exception as e:
            print("failed.\n  %s" % re.sub(r"\s+", " ", str(e)))
            print("  Checks: is IMAP enabled for the account? Is it an app password, not your normal password?")
            print("  For a school Google Workspace account the admin may have disabled app passwords; forwarding")
            print("  that mail to your Gmail is the simplest workaround.")
            sys.exit(1)
        print("ok.")
        where = password_set(acct, pw, cfg)
        if acct not in cfg["accounts"]:
            cfg["accounts"].append(acct)
        save_config(cfg)
        print("saved %s (%s). Run: today scan" % (acct, "Keychain" if where == "keychain" else "config.json, mode 600"))
        return
    if cmd in ("rm", "remove"):
        acct = (args[1] if len(args) > 1 else "").lower()
        password_del(acct, cfg)
        cfg["accounts"] = [a for a in cfg["accounts"] if a != acct]
        save_config(cfg)
        print("removed %s" % acct)
        return
    sys.exit("usage: today mail [list | add EMAIL | rm EMAIL]")


def cli_scan(args=()):
    cfg = load_config()
    if not cfg["accounts"]:
        sys.exit("no mail accounts yet — today mail add you@gmail.com")
    res = scan_all(cfg, progress=lambda t: print("  " + t), history="--history" in args)
    for a, e in res["errors"].items():
        print("  ! %s: %s" % (a, e))
    print("found %d new relevant message%s" % (res["found"], "" if res["found"] == 1 else "s"))
    t = res["tracker"]
    print("applications: %d total · %d active · %d rejected" % (t["applications"], t["active"], t["rejected"]))
    print_inbox(res["inbox"])


def cli_apps(args):
    TR = _tracker()
    tr = TR.load(APP_DIR)
    if not tr["apps"] and not tr["seen"]:
        TR.ingest_items(tr, load_inbox()["items"])
        TR.save(APP_DIR, tr)
    t = TR.totals(tr)
    print("%d applications · %d active (%d waiting, %d in progress) · %d rejected · %d reached interview · %d offer%s"
          % (t["applications"], t["active"], t["waiting"], t["in_progress"], t["rejected"], t["reached_interview"],
             t["offers"], "" if t["offers"] == 1 else "s"))
    rows = [TR.derive(a) for a in tr["apps"]]
    if "--all" not in args:
        rows = [r for r in rows if r["status"] == "active" and r["stage"] != "applied"]
        print("in progress (today apps --all for everything):")
    rows.sort(key=lambda r: r["last"], reverse=True)
    for r in rows:
        tail = r["status_label"] if r["status"] != "active" else ("since " + short_date(r["stage_date"] or r["applied"]))
        print("  %-12s %-18s %-44s %s" % (clip(r["stage_label"], 12), clip(r["company"], 18), clip(r["role"] or "-", 44), tail))


def main(argv):
    args = list(argv)
    date_str = today_str()
    if args and args[0] == "tomorrow":
        date_str = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        args = args[1:] or ["show"]
    elif args and args[0] == "--date" and len(args) > 1:
        date_str = args[1]
        args = args[2:] or ["show"]
    if not args:
        if sys.stdout.isatty() and sys.stdin.isatty():
            return run_tui(date_str)
        return print_plan(load_plan(date_str), load_daily(), load_deadlines(), load_inbox())
    cmd = args[0]
    if cmd in ("help", "-h", "--help"):
        print(__doc__.strip())
    elif cmd in ("version", "--version"):
        print("today %s" % VERSION)
    elif cmd == "daily":
        cli_daily(args[1:], date_str)
    elif cmd in ("deadline", "deadlines"):
        cli_deadline(args[1:])
    elif cmd == "mail":
        cli_mail(args[1:])
    elif cmd == "scan":
        cli_scan(args[1:])
    elif cmd in ("apps", "applications"):
        cli_apps(args[1:])
    elif cmd == "inbox":
        print_inbox(load_inbox(), show_all="--all" in args)
    elif cmd == "ui":
        return run_tui(date_str)
    elif cmd == "where":
        print(APP_DIR)
    else:
        cli_plan(args, date_str)


# ----------------------------------------------------------------------------
# TUI
# ----------------------------------------------------------------------------

def run_tui(date_str):
    try:
        import curses  # noqa: F401
    except ImportError:
        print_plan(load_plan(date_str), load_daily(), load_deadlines(), load_inbox())
        return
    import locale
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")
    import curses
    try:
        curses.wrapper(lambda scr: UI(scr, date_str).loop())
    except KeyboardInterrupt:
        pass


class UI(object):
    PANELS = ("schedule", "daily", "inbox")
    HINTS = {
        0: "↑↓ move   space done   a add   t time   e edit   n note   +/- ±15m   x remove   c carry   tab panel   s scan   ? help   q quit",
        1: "↑↓ move   space done   +/- count   a add   e edit   x remove   tab panel   s scan   ? help   q quit",
        2: "↑↓ move   enter open in Gmail   m done   x hide   s scan   tab panel   ? help   q quit",
    }

    def __init__(self, scr, date_str):
        import curses
        self.curses = curses
        self.scr = scr
        self.date = date_str
        self.follow_today = date_str == today_str()
        self.panel = 0
        self.cur = [0, 0, 0]
        self.off = [0, 0, 0]
        self.msg = ""
        self.msg_until = 0.0
        self.pending_remove = None
        curses.curs_set(0)
        scr.keypad(True)
        scr.timeout(1000)
        self.init_colors()
        self.reload()
        if not self.plan["items"]:
            items, src = carry_candidates(self.date)
            if items:
                self.say("%d unfinished from %s — press c to carry them over" % (len(items), short_date(src)), 12)

    # ---- state ---------------------------------------------------------------
    def reload(self):
        self.plan = load_plan(self.date)
        self.daily = load_daily()
        self.deadlines = load_deadlines()
        self.inbox = load_inbox()
        self.cfg = load_config()

    def say(self, text, secs=4):
        self.msg = text
        self.msg_until = time.time() + secs

    def init_colors(self):
        c = self.curses
        self.C = {"accent": 0, "warn": 0, "danger": 0, "good": 0, "dim": c.A_DIM, "bold": c.A_BOLD,
                  "badge": c.A_REVERSE | c.A_BOLD, "label": c.A_BOLD | c.A_DIM, "label_on": c.A_BOLD, "sel": c.A_REVERSE}
        try:
            if not c.has_colors():
                return
            c.start_color()
            c.use_default_colors()
            c.init_pair(1, c.COLOR_CYAN, -1)
            c.init_pair(2, c.COLOR_YELLOW, -1)
            c.init_pair(3, c.COLOR_RED, -1)
            c.init_pair(4, c.COLOR_GREEN, -1)
            c.init_pair(5, c.COLOR_BLACK, c.COLOR_CYAN)
            c.init_pair(6, c.COLOR_MAGENTA, -1)
            self.C.update({"accent": c.color_pair(1), "warn": c.color_pair(2) | c.A_BOLD, "danger": c.color_pair(3) | c.A_BOLD,
                           "good": c.color_pair(4), "badge": c.color_pair(5) | c.A_BOLD, "label_on": c.color_pair(1) | c.A_BOLD,
                           "alt": c.color_pair(6)})
        except Exception:
            pass

    def sev_attr(self, sev):
        return {"soon": self.C["danger"], "over": self.C["danger"], "near": self.C["warn"], "ok": self.C["accent"],
                "none": self.C["dim"], "done": self.C["dim"]}.get(sev, 0)

    # ---- drawing helpers -------------------------------------------------------
    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w or x < 0:
            return
        s = str(s)
        room = w - x - (1 if y == h - 1 else 0)
        if room <= 0:
            return
        s = s[:room]
        try:
            self.scr.addstr(y, x, s, attr)
        except self.curses.error:
            pass

    def fill(self, y, x, width, attr):
        self.put(y, x, " " * max(0, width), attr)

    # ---- lists ----------------------------------------------------------------
    def sched(self):
        return sorted_items(self.plan["items"])

    def dailies(self):
        return self.daily["items"]

    def inbox_items(self):
        return inbox_visible(self.inbox)

    def current_list(self):
        return (self.sched(), self.dailies(), self.inbox_items())[self.panel]

    def clamp(self, p, n):
        if n == 0:
            self.cur[p] = 0
            self.off[p] = 0
        else:
            self.cur[p] = max(0, min(self.cur[p], n - 1))
            self.off[p] = max(0, min(self.off[p], self.cur[p]))

    # ---- main draw --------------------------------------------------------------
    def draw(self):
        scr, C = self.scr, self.C
        scr.erase()
        h, w = scr.getmaxyx()
        sched, dailies, inbox, dls = self.sched(), self.dailies(), self.inbox_items(), all_deadlines(self.deadlines, self.inbox)
        for p, lst in enumerate((sched, dailies, inbox)):
            self.clamp(p, len(lst))

        # header
        badge = " TODAY "
        self.put(0, 0, badge, C["badge"])
        title = human_date(self.date) + ("" if self.date == today_str() else "   (not today)")
        self.put(0, len(badge) + 1, title, C["bold"])
        done = sum(1 for i in sched if i.get("done"))
        total = len(sched)
        clock = now().strftime("%I:%M %p").lstrip("0")
        right = clock
        if total:
            right = "%d/%d  %s   %s" % (done, total, bar_text(done, total, 8), clock)
        self.put(0, max(len(badge) + len(title) + 3, w - len(right) - 1), right, C["dim"])
        self.put(1, 0, "─" * (w - 1), C["dim"])

        # deadlines
        y = 2
        nd_max = 5 if h >= 36 else (4 if h >= 30 else 2)
        self.put(y, 0, "DEADLINES", C["label"])
        hot = [d for d in dls if not d["done"] and d["sev"] in ("soon", "over")]
        if hot:
            self.put(y, 11, "· %s within 24h" % plural(len(hot), "deadline"), C["danger"])
        y += 1
        if not dls:
            self.put(y, 2, "none — today deadline add \"Name\" \"2026-10-01 11:27\" (open OAs found in mail appear here too)", C["dim"])
            y += 1
        else:
            for d in dls[:nd_max]:
                self.draw_deadline(y, d, w)
                y += 1
            if len(dls) > nd_max:
                self.put(y, 2, "+%d more   (today deadline)" % (len(dls) - nd_max), C["dim"])
                y += 1
        y += 1

        # layout for the rest: the schedule/daily block takes what it needs, the inbox gets the slack
        footer_y = h - 1
        main_top = y
        avail = footer_y - main_top - 1          # rows for main + one blank + inbox label + inbox rows
        two_col = w >= 100
        sched_rows = len(sched) + 1 + (1 if any(i.get("note") for i in sched) else 0)
        if two_col:
            needed_main = max(sched_rows, len(dailies) + 1, 5)
        else:
            needed_main = sched_rows + 1 + min(len(dailies) + 1, 4)
        n_inbox = len(inbox)
        inbox_rows = max(1, min(max(n_inbox, 1), avail - needed_main - 2))
        main_h = avail - inbox_rows - 2
        if main_h < 3:
            self.put(main_top, 0, "terminal too small — make it taller", C["warn"])
            self.draw_footer(footer_y, w)
            return
        inbox_h = 1 + inbox_rows
        if two_col:
            lw = int(w * 0.58)
            self.draw_schedule(main_top, 0, lw - 1, main_h, sched)
            for yy in range(main_top, main_top + main_h):
                self.put(yy, lw, "│", C["dim"])
            self.draw_daily(main_top, lw + 2, w - lw - 3, main_h, dailies)
        else:
            dh = min(len(dailies) + 1, max(3, main_h // 3))
            sh = main_h - dh - 1
            self.draw_schedule(main_top, 0, w - 1, sh, sched)
            self.draw_daily(main_top + sh + 1, 0, w - 1, dh, dailies)
        self.draw_inbox(footer_y - inbox_h, w, inbox_rows, inbox)
        self.draw_footer(footer_y, w)

    def draw_deadline(self, y, d, w):
        C = self.C
        mark = "✓" if d["done"] else ("●" if d["sev"] in ("soon", "over") else "○")
        attr = self.sev_attr(d["sev"])
        name_w = max(14, min(30, w - 52))
        x_when = 4 + name_w + 2
        x_until = x_when + 24
        self.put(y, 2, mark, attr)
        self.put(y, 4, clip(d["name"], name_w), C["dim"] if d["done"] else 0)
        when = deadline_when(d) if (d["done"] or d["at"]) else "rolling — no deadline"
        self.put(y, x_when, clip(when, 22), C["dim"] if d["done"] else C["accent"])
        if not d["done"]:
            self.put(y, x_until, d["until"], attr)
            if d.get("note") and w > x_until + 40:
                self.put(y, x_until + 18, clip(d["note"], w - x_until - 19), C["dim"])
        else:
            self.put(y, x_until, "done", C["dim"])

    def draw_schedule(self, y, x, width, height, items):
        C = self.C
        focused = self.panel == 0
        self.put(y, x, "SCHEDULE", C["label_on"] if focused else C["label"])
        left = sum(1 for i in items if not i.get("done"))
        if items:
            self.put(y, x + 10, "%s left" % left if left else "all done ✓", C["good"] if not left else C["dim"])
        rows = height - 1
        if not items:
            self.put(y + 1, x + 2, "nothing planned — press a to add", C["dim"])
            return
        cur, off = self.cur[0], self.off[0]

        def last_visible(start):
            used = 0
            last = start - 1
            for idx in range(start, len(items)):
                cost = 2 if (idx == cur and items[idx].get("note") and focused) else 1
                if used + cost > rows:
                    break
                used += cost
                last = idx
            return last

        while cur > last_visible(off):
            off += 1
        self.off[0] = off
        yy = y + 1
        for idx in range(off, len(items)):
            if yy >= y + height:
                break
            it = items[idx]
            sel = focused and idx == cur
            base = C["sel"] if sel else 0
            if sel:
                self.fill(yy, x, width, base)
            dim = C["dim"] if (it.get("done") and not sel) else 0
            self.put(yy, x, "▸" if sel else " ", base)
            t = fmt_time(it.get("time")).rjust(8) if it.get("time") else "       –"
            self.put(yy, x + 2, t, base if sel else (C["dim"] if it.get("done") else C["accent"]))
            self.put(yy, x + 11, "✓" if it.get("done") else " ", base | (C["good"] if not sel and it.get("done") else 0))
            tail = ""
            if it.get("mins"):
                tail += " · " + fmt_minutes(it["mins"])
            if it.get("mins") and it.get("time"):
                tail += " → " + fmt_time(add_minutes(it["time"], it["mins"]))
            title_room = width - 13 - (3 if it.get("urgent") else 0)
            title = clip(it["title"], max(4, title_room - len(tail)))
            self.put(yy, x + 13, title, base | dim | (C["bold"] if not it.get("done") and not sel else 0))
            self.put(yy, x + 13 + len(title), tail, base | (C["dim"] if not sel else 0))
            if it.get("urgent") and not it.get("done"):
                self.put(yy, x + width - 2, "⚠", base | (C["danger"] if not sel else 0))
            yy += 1
            if sel and it.get("note") and yy < y + height:
                self.fill(yy, x, width, base)
                self.put(yy, x + 13, "└ " + clip(it["note"], width - 16), base)
                yy += 1

    def draw_daily(self, y, x, width, height, items):
        C = self.C
        focused = self.panel == 1
        self.put(y, x, "DAILY", C["label_on"] if focused else C["label"])
        if items:
            ndone = sum(1 for it in items if daily_state(self.daily, it, self.date)[0])
            self.put(y, x + 7, "%d/%d" % (ndone, len(items)), C["good"] if ndone == len(items) else C["dim"])
        rows = height - 1
        if not items:
            self.put(y + 1, x + 2, "no daily tasks — press a to add", C["dim"])
            return
        cur, off = self.cur[1], self.off[1]
        if cur >= off + rows:
            off = cur - rows + 1
        self.off[1] = off
        for r in range(rows):
            idx = off + r
            if idx >= len(items):
                break
            it = items[idx]
            sel = focused and idx == cur
            base = C["sel"] if sel else 0
            if sel:
                self.fill(y + 1 + r, x, width, base)
            dn, cnt = daily_state(self.daily, it, self.date)
            self.put(y + 1 + r, x, "▸" if sel else " ", base)
            if it.get("target"):
                tgt = it["target"]
                b = bar_text(cnt, tgt, 10)
                self.put(y + 1 + r, x + 2, b, base | (C["good"] if dn and not sel else (C["accent"] if not sel else 0)))
                cnttxt = "%2d/%-3d" % (cnt, tgt)
                self.put(y + 1 + r, x + 13, cnttxt, base | (C["dim"] if not sel else 0))
                self.put(y + 1 + r, x + 20, clip(it["title"], width - 21), base | (C["dim"] if dn and not sel else 0))
            else:
                self.put(y + 1 + r, x + 2, "[✓]" if dn else "[ ]", base | (C["good"] if dn and not sel else 0))
                self.put(y + 1 + r, x + 6, clip(it["title"], width - 7), base | (C["dim"] if dn and not sel else 0))

    def draw_inbox(self, y, w, rows, items):
        C = self.C
        focused = self.panel == 2
        self.put(y, 0, "INBOX", C["label_on"] if focused else C["label"])
        summ = inbox_summary(self.inbox)
        self.put(y, 7, clip(summ, w - 8), C["dim"])
        meta = []
        if self.inbox.get("errors"):
            meta.append("! " + "; ".join("%s: %s" % (a, clip(e, 40)) for a, e in self.inbox["errors"].items()))
        meta.append(("scanned " + self.inbox["updated"]) if self.inbox.get("updated") else "never scanned")
        if self.cfg.get("accounts"):
            meta.append(", ".join(self.cfg["accounts"]))
        else:
            meta.append("no accounts: today mail add EMAIL")
        meta_s = "  ·  ".join(meta)
        room = w - 1 - (7 + len(summ) + 4)
        if room >= 24:
            meta_s = clip(meta_s, room)
            self.put(y, w - len(meta_s) - 1, meta_s, C["danger"] if self.inbox.get("errors") else C["dim"])
        if not items:
            self.put(y + 1, 2, "nothing found yet — press s to scan mail", C["dim"])
            return
        cur, off = self.cur[2], self.off[2]
        if cur >= off + rows:
            off = cur - rows + 1
        self.off[2] = off
        for r in range(rows):
            idx = off + r
            if idx >= len(items):
                break
            i = items[idx]
            sel = focused and idx == cur
            base = C["sel"] if sel else 0
            yy = y + 1 + r
            if sel:
                self.fill(yy, 0, w - 1, base)
            kind = i.get("kind")
            done = i.get("status") == "done"
            label = "OA ✓" if kind == "oa" and done else KIND_LABEL.get(kind, "?")
            lattr = {"oa": C["accent"], "interview": C["good"], "offer": C["good"], "rejection": C["dim"],
                     "important": C["warn"], "oa_done": C["dim"]}.get("oa_done" if done and kind == "oa" else kind, 0)
            self.put(yy, 0, "▸" if sel else " ", base)
            self.put(yy, 2, label.ljust(5), base | (lattr if not sel else 0) | (C["bold"] if kind in ("interview", "offer") and not sel else 0))
            self.put(yy, 8, clip(i.get("company") or "", 16).ljust(16), base | (C["bold"] if not sel and not done and kind != "rejection" else 0))
            right = ""
            rattr = C["dim"]
            if i.get("due") and i.get("status") == "open":
                txt, sev = until(i["due"])
                right = "%s · %s" % (fmt_when(i["due"]), txt)
                rattr = self.sev_attr(sev)
            elif i.get("detail") and kind == "oa" and not done:
                right = clip(i["detail"], 30)
            else:
                right = short_date(i.get("date", ""))
            title_w = w - 26 - len(right) - 3
            self.put(yy, 25, clip(i.get("title", ""), title_w), base | (C["dim"] if (done or kind == "rejection") and not sel else 0))
            self.put(yy, w - len(right) - 1, right, base | (rattr if not sel else 0))

    def draw_footer(self, y, w):
        C = self.C
        if self.msg and time.time() < self.msg_until:
            self.put(y, 0, clip(self.msg, w - 1), C["bold"])
        elif self.pending_remove:
            self.put(y, 0, clip("Remove \"%s\"?  y / n" % self.pending_remove[1], w - 1), C["warn"])
        else:
            self.put(y, 0, clip(self.HINTS[self.panel], w - 1), C["dim"])

    # ---- prompt -------------------------------------------------------------------
    def prompt(self, label, initial=""):
        curses = self.curses
        scr = self.scr
        buf = list(initial)
        pos = len(buf)
        curses.curs_set(1)
        scr.timeout(-1)
        try:
            while True:
                h, w = scr.getmaxyx()
                self.fill(h - 1, 0, w - 1, 0)
                line = label + "".join(buf)
                self.put(h - 1, 0, line, self.C["bold"])
                try:
                    scr.move(h - 1, min(len(label) + pos, w - 2))
                except curses.error:
                    pass
                scr.refresh()
                try:
                    ch = scr.get_wch()
                except AttributeError:
                    ch = scr.getch()
                    if 32 <= ch < 256:
                        ch = chr(ch)
                except curses.error:
                    continue
                if isinstance(ch, str):
                    if ch in ("\n", "\r"):
                        return "".join(buf).strip()
                    if ch == "\x1b":
                        return None
                    if ch in ("\x7f", "\b"):
                        if pos > 0:
                            del buf[pos - 1]
                            pos -= 1
                    elif ch == "\x15":
                        buf = []
                        pos = 0
                    elif ch == "\x01":
                        pos = 0
                    elif ch == "\x05":
                        pos = len(buf)
                    elif ch == "\x0b":
                        del buf[pos:]
                    elif ch.isprintable():
                        buf.insert(pos, ch)
                        pos += 1
                else:
                    if ch in (curses.KEY_BACKSPACE, 127, 8):
                        if pos > 0:
                            del buf[pos - 1]
                            pos -= 1
                    elif ch == curses.KEY_DC:
                        del buf[pos:pos + 1]
                    elif ch == curses.KEY_LEFT:
                        pos = max(0, pos - 1)
                    elif ch == curses.KEY_RIGHT:
                        pos = min(len(buf), pos + 1)
                    elif ch == curses.KEY_HOME:
                        pos = 0
                    elif ch == curses.KEY_END:
                        pos = len(buf)
                    elif ch == curses.KEY_ENTER:
                        return "".join(buf).strip()
        finally:
            curses.curs_set(0)
            scr.timeout(1000)

    def help_overlay(self):
        lines = [
            "today — keys",
            "",
            "  tab / 1 2 3     switch panel (schedule, daily, inbox)",
            "  ↑ ↓  j k        move          r  reload files",
            "  space / enter   toggle done   (inbox: enter opens the mail in Gmail)",
            "  a               add           schedule: \"6:10pm Norvia HackerRank\"  · daily: a number in the title makes a counter",
            "  e               edit title    n  edit note (schedule)",
            "  t               set time      \"6:10pm 75\" also sets a 75-minute duration",
            "  + / -           schedule: shift time ±15 min   · daily: counter ±1",
            "  x / delete      remove (asks y/n)   · inbox: hide",
            "  m               inbox: mark assessment done / undone",
            "  c               carry unfinished items over from the previous plan",
            "  s               scan mail (accounts: today mail add you@gmail.com)",
            "  q               quit",
            "",
            "  data: " + APP_DIR,
            "  any key to close",
        ]
        h, w = self.scr.getmaxyx()
        bw = min(w - 2, max(len(l) for l in lines) + 4)
        bh = min(h - 2, len(lines) + 2)
        y0 = max(0, (h - bh) // 2)
        x0 = max(0, (w - bw) // 2)
        for yy in range(bh):
            self.fill(y0 + yy, x0, bw, self.C["sel"])
        for i, l in enumerate(lines[: bh - 2]):
            self.put(y0 + 1 + i, x0 + 2, clip(l, bw - 4), self.C["sel"] | (self.C["bold"] if i == 0 else 0))
        self.scr.refresh()
        self.scr.timeout(-1)
        self.scr.getch()
        self.scr.timeout(1000)

    # ---- actions ------------------------------------------------------------------
    def open_link(self, url):
        if not url:
            self.say("no link for this item")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say("opened in browser")
        except OSError:
            self.say(url, 10)

    def do_scan(self):
        if not self.cfg.get("accounts"):
            self.say("No mail accounts yet — quit and run: today mail add you@gmail.com", 10)
            return

        def progress(t):
            self.say(t, 60)
            self.draw()
            self.scr.refresh()

        try:
            res = scan_all(self.cfg, progress)
        except Exception as e:
            self.say("Scan failed: %s" % e, 10)
            return
        self.reload()
        if res["errors"]:
            self.say("Scan done with problems: " + "; ".join("%s — %s" % (a, e) for a, e in res["errors"].items()), 15)
        else:
            self.say("Scan done · %s" % inbox_summary(self.inbox), 8)

    def add_schedule(self):
        s = self.prompt("Add (time then title, e.g. 6:10pm Norvia HackerRank): ")
        if not s:
            return
        hhmm, rest = split_time_prefix(s.split())
        title = " ".join(rest).strip()
        if not title:
            self.say("need a title")
            return
        it = add_item(self.plan, title, hhmm)
        save_plan(self.plan)
        lst = self.sched()
        self.cur[0] = next((k for k, x in enumerate(lst) if x["id"] == it["id"]), 0)
        self.say("added %s%s" % (title, " at " + fmt_time(hhmm) if hhmm else " (no time — press t to set one)"))

    def add_daily(self):
        s = self.prompt("Add daily task (a number like 20 makes it a counter): ")
        if not s:
            return
        target = guess_target(s)
        self.daily["items"].append({"id": new_id(), "title": s, "target": target})
        save_daily(self.daily)
        self.cur[1] = len(self.daily["items"]) - 1
        self.say("added %s%s" % (s, "  (counter to %d — use + / -)" % target if target else ""))

    def handle(self, ch):
        curses = self.curses
        if self.pending_remove:
            if ch in (ord("y"), ord("Y")):
                self.confirm_remove()
            else:
                self.say("kept")
            self.pending_remove = None
            return True
        if ch == ord("q"):
            return False
        if ch in (ord("\t"), 9):
            self.panel = (self.panel + 1) % 3
        elif ch == curses.KEY_BTAB:
            self.panel = (self.panel - 1) % 3
        elif ch in (ord("1"), ord("2"), ord("3")):
            self.panel = ch - ord("1")
        elif ch in (curses.KEY_UP, ord("k")):
            self.cur[self.panel] -= 1
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.cur[self.panel] += 1
        elif ch == curses.KEY_HOME:
            self.cur[self.panel] = 0
        elif ch == curses.KEY_END:
            self.cur[self.panel] = 10 ** 6
        elif ch == ord("?"):
            self.help_overlay()
        elif ch == ord("r"):
            self.reload()
            self.say("reloaded")
        elif ch == ord("s"):
            self.do_scan()
        elif ch == ord("c"):
            n, src = carry_over(self.plan)
            save_plan(self.plan)
            self.say("carried %d item%s over from %s" % (n, "" if n == 1 else "s", short_date(src) if src else "nowhere"))
        elif ch == ord("a"):
            if self.panel == 0:
                self.add_schedule()
            elif self.panel == 1:
                self.add_daily()
            else:
                self.say("inbox items come from the mail scan — press s")
        elif ch in (ord(" "), ord("\n"), ord("\r"), curses.KEY_ENTER, ord("m")):
            self.toggle(ch)
        elif ch in (ord("x"), curses.KEY_DC):
            self.ask_remove()
        elif ch == ord("e"):
            self.edit_title()
        elif ch == ord("n") and self.panel == 0:
            self.edit_note()
        elif ch == ord("t") and self.panel == 0:
            self.set_time()
        elif ch in (ord("+"), ord("="), ord("-"), ord("_")):
            self.nudge(1 if ch in (ord("+"), ord("=")) else -1)
        elif ch == ord("u") and self.panel == 0:
            it = self.selected()
            if it:
                it["urgent"] = not it.get("urgent")
                save_plan(self.plan)
        return True

    def selected(self):
        lst = self.current_list()
        if not lst:
            return None
        self.clamp(self.panel, len(lst))
        return lst[self.cur[self.panel]]

    def toggle(self, ch):
        it = self.selected()
        if not it:
            return
        if self.panel == 0:
            it["done"] = not it.get("done")
            save_plan(self.plan)
            if it["done"]:
                lst = self.sched()
                nxt = next((k for k, x in enumerate(lst) if not x.get("done")), None)
                left = sum(1 for x in lst if not x.get("done"))
                self.say("✓ %s%s" % (it["title"], ("   — %s left" % left) if left else "   — all done, nice.") , 3)
                if nxt is not None:
                    self.cur[0] = nxt
        elif self.panel == 1:
            dn, cnt = daily_state(self.daily, it, self.date)
            daily_set(self.daily, it, self.date, done=not dn)
            save_daily(self.daily)
        else:
            if ch == ord("m"):
                if it.get("kind") in ("oa", "oa_done"):
                    for i in self.inbox["items"]:
                        if i["id"] == it["id"]:
                            i["status"] = "open" if i.get("status") == "done" else "done"
                            i["kind"] = "oa"
                            self.say("%s marked %s" % (i.get("company"), i["status"]))
                    save_inbox(self.inbox)
                else:
                    self.say("m marks assessments done; x hides other items")
            else:
                self.open_link(it.get("link"))

    def ask_remove(self):
        it = self.selected()
        if not it:
            return
        if self.panel == 2:
            for i in self.inbox["items"]:
                if i["id"] == it["id"]:
                    i["status"] = "hidden"
            save_inbox(self.inbox)
            self.say("hidden (today inbox --all shows hidden items)")
            return
        self.pending_remove = (self.panel, it["title"], it["id"])

    def confirm_remove(self):
        panel, title, iid = self.pending_remove
        if panel == 0:
            self.plan["items"] = [i for i in self.plan["items"] if i["id"] != iid]
            save_plan(self.plan)
        elif panel == 1:
            self.daily["items"] = [i for i in self.daily["items"] if i["id"] != iid]
            save_daily(self.daily)
        self.say("removed %s" % title)

    def edit_title(self):
        it = self.selected()
        if not it or self.panel == 2:
            return
        s = self.prompt("Title: ", it["title"])
        if s is None or not s:
            return
        it["title"] = s
        if self.panel == 0:
            save_plan(self.plan)
        else:
            if it.get("target") is None:
                it["target"] = guess_target(s)
            save_daily(self.daily)

    def edit_note(self):
        it = self.selected()
        if not it:
            return
        s = self.prompt("Note: ", it.get("note", ""))
        if s is None:
            return
        it["note"] = s
        save_plan(self.plan)

    def set_time(self):
        it = self.selected()
        if not it:
            return
        cur = fmt_time(it.get("time")) if it.get("time") else ""
        if it.get("mins"):
            cur += " %d" % it["mins"]
        s = self.prompt("Time [minutes]  (e.g. 6:10pm 75, or blank for no time): ", cur)
        if s is None:
            return
        if not s:
            it["time"] = ""
            save_plan(self.plan)
            return
        toks = s.split()
        mins = None
        if len(toks) >= 2 and toks[-1].isdigit() and not re.fullmatch(r"[ap]\.?m\.?", toks[-1], re.I):
            mins = int(toks.pop())
        hhmm = parse_time(" ".join(toks))
        if not hhmm:
            self.say("time not understood: %r" % s)
            return
        it["time"] = hhmm
        if mins is not None:
            it["mins"] = mins or None
        save_plan(self.plan)
        lst = self.sched()
        self.cur[0] = next((k for k, x in enumerate(lst) if x["id"] == it["id"]), 0)

    def nudge(self, sign):
        it = self.selected()
        if not it:
            return
        if self.panel == 0:
            if not it.get("time"):
                self.say("no time set — press t")
                return
            it["time"] = add_minutes(it["time"], 15 * sign)
            save_plan(self.plan)
            lst = self.sched()
            self.cur[0] = next((k for k, x in enumerate(lst) if x["id"] == it["id"]), 0)
        elif self.panel == 1:
            dn, cnt = daily_state(self.daily, it, self.date)
            if it.get("target") is None:
                daily_set(self.daily, it, self.date, done=sign > 0)
            else:
                daily_set(self.daily, it, self.date, count=cnt + sign)
                dn2, cnt2 = daily_state(self.daily, it, self.date)
                if dn2 and not dn:
                    self.say("%s — done for today ✓" % it["title"], 3)
            save_daily(self.daily)

    # ---- loop -------------------------------------------------------------------
    def loop(self):
        curses = self.curses
        while True:
            self.draw()
            self.scr.refresh()
            ch = self.scr.getch()
            if ch == -1:
                # clock tick; at midnight, roll over to the new day if we were showing "today"
                if self.follow_today and self.date != today_str():
                    self.date = today_str()
                    self.reload()
                    if not self.plan["items"]:
                        items, src = carry_candidates(self.date)
                        if items:
                            self.say("New day. %d unfinished from %s — press c to carry them over" % (len(items), short_date(src)), 20)
                continue
            if ch == curses.KEY_RESIZE:
                continue
            if not self.handle(ch):
                return


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:
        pass
