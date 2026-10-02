# -*- coding: utf-8 -*-
"""The inbox: what the mail scan found (inbox.json), how a new scan merges into it, and views over it.

Public: load_inbox, save_inbox, merge_inbox, inbox_visible, inbox_counts, inbox_summary, assessment_deadlines,
KIND_LABEL (and INBOX_FILE). Items are keyed by message key; a rescan keeps each item's id and status.
"""

import datetime as dt
import os

from store import APP_DIR, load_json, save_json
from textutil import plural
from timeutil import now, to_date
from tracker import company_key, same_company

__all__ = ["INBOX_FILE", "KIND_LABEL", "load_inbox", "save_inbox", "merge_inbox", "inbox_visible", "inbox_counts",
           "inbox_summary", "assessment_deadlines", "event_deadlines"]


INBOX_FILE = os.path.join(APP_DIR, "inbox.json")


KIND_LABEL = {"offer": "OFFER", "interview": "INTVW", "oa": "OA", "oa_done": "OA ✓",
              "important": "NOTE", "rejection": "REJ", "applied": "APPLD", "event": "EVENT"}


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
    if k == "event":
        return 2
    if k == "oa":
        return 5 if st == "done" else (3 if i.get("due") else 4)
    if k == "important":
        return 6
    if k == "rejection":
        return 7
    return 8


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
    c = {"offer": 0, "interview": 0, "oa_open": 0, "oa_done": 0, "important": 0, "rejection": 0, "applied": 0,
         "event": 0}
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
    if c["event"]:
        parts.append("%d event%s" % (c["event"], "" if c["event"] == 1 else "s"))
    parts.append(plural(c["rejection"], "rejection"))
    if c["important"]:
        parts.append("%d flagged" % c["important"])
    if c["applied"]:
        parts.append("%d applied" % c["applied"])
    return " · ".join(parts) + " (30d)"


def _same_assessment(a, b):
    """Are these two open assessments at one company the same one, seen twice?

    When both name a closing day, that day decides: a reminder points at the same day however long after
    the invite it arrives, and two different closing days are two different assessments however close
    together the mails are. With no day to go on, fall back to the mails arriving within a few days, which
    is the invite-plus-platform-mail pair.
    """
    da, db = (a.get("due") or "")[:10], (b.get("due") or "")[:10]
    if da and db:
        return da == db
    return abs((to_date(a["date"]) - to_date(b["date"])).days) <= 3


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
    # An OA is done once the platform confirmed a submission for that company. The invite and the
    # confirmation seldom spell the company the same way ("Akunacapital" / "Akuna Capital", "IMC Trading" /
    # "IMC"), so this asks the tracker's matcher rather than comparing strings, which left finished
    # assessments open forever.
    done_cos = [c for c in ((i.get("company") or "") for i in items if i.get("kind") == "oa_done") if c]
    for i in items:
        if i.get("kind") != "oa" or i.get("status") != "open":
            continue
        mine = i.get("company") or ""
        if mine and any(same_company(mine, c) for c in done_cos):
            i["status"] = "done"
    # collapse duplicate open OAs for one company: the invite and the platform mail arrive within a few
    # days of each other, and a reminder about the same assessment arrives whenever the platform feels
    # like it but points at the same closing day. Without the second rule a last-day reminder becomes a
    # second row that contradicts the first.
    items.sort(key=lambda i: (i.get("date", ""), 1 if i.get("due") else 0, 1 if i.get("platform") else 0))
    seen = {}
    keep = []
    for i in reversed(items):
        if i.get("kind") == "oa" and i.get("status") != "hidden":
            k = company_key(i.get("company") or "")      # same matcher as the done rule above
            prev = seen.get(k)
            if prev is not None and _same_assessment(prev, i):
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


def event_deadlines(inbox):
    """Invitations that have not happened yet, as deadline rows, so the deadlines slice never needs to know
    the inbox layout. Same shape as assessment_deadlines."""
    out = []
    today = dt.date.today().isoformat()
    for i in inbox["items"]:
        if i.get("kind") != "event" or i.get("status") == "hidden":
            continue
        at = i.get("due", "")
        if not at or at[:10] < today:
            continue
        out.append({"id": i["id"], "name": i.get("title") or "Event", "at": at, "note": i.get("detail", ""),
                    "done": i.get("status") == "done", "source": "inbox", "date": i.get("date", "")})
    return out


def assessment_deadlines(inbox):
    """Open assessments (and ones finished in the last week) as deadline rows, so the deadlines slice never needs
    to know the inbox layout: [{"id", "name", "at", "note", "done", "source": "inbox", "date"}]."""
    out = []
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
    return out
