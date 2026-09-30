# -*- coding: utf-8 -*-
"""Time and date parsing and formatting for `today`. Leaf module: depends on nothing but the standard library.

Public: now, today_str, to_date, human_date, short_date, parse_time, split_time_prefix, fmt_time, add_minutes,
parse_when, when_dt, fmt_when, until, fmt_minutes (and the DOW / MONTHS / MON3 name tables).
"""

import datetime as dt
import re


DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


MON3 = [m[:3] for m in MONTHS]


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


def fmt_minutes(n):
    if not n:
        return ""
    if n >= 60 and n % 60 == 0:
        return "%dh" % (n // 60)
    if n > 90:
        return "%dh %02dm" % (n // 60, n % 60)
    return "%dm" % n
