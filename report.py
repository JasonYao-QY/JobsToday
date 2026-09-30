# -*- coding: utf-8 -*-
"""Reporting an application yourself ("I applied to Acme, SWE Intern"), for the CLI and the window alike.

It touches two slices through their public functions: the application tracker gets an "applied" event with
source "reported", and the daily counter that counts applications goes up by one, but only when the report
is a new application. A confirmation email that arrives later joins the same record through the tracker's
own matching (same company, similar role), so nothing is counted twice.

Public: report_applied(company, role, date=None, link="")
"""

import re

import daily
import tracker
from store import APP_DIR, new_id
from timeutil import today_str


def _applications_counter(d):
    """The daily item that counts applications: one marked {"counts": "applications"}, else the first counter
    whose title talks about applying ("Apply for 20 simplifyjob positions")."""
    for it in d["items"]:
        if it.get("counts") == "applications":
            return it
    for it in d["items"]:
        if it.get("target") and re.search(r"\bappl(?:y|ied|ying|ications?)\b", it.get("title", ""), re.I):
            return it
    return None


def report_applied(company, role, date=None, link=""):
    """-> {"app_id", "new", "company", "role", "daily": {"title", "count", "target"} or None}"""
    company, role = (company or "").strip(), (role or "").strip()
    if not company:
        raise ValueError("which company?")
    date = date or today_str()
    tr = tracker.load(APP_DIR)
    before = len(tr["apps"])
    app = tracker.ingest(tr, {"key": "reported:" + new_id(), "date": date, "kind": "applied", "company": company,
                              "role": role, "subject": "Reported: applied", "link": link, "account": "",
                              "platform": "", "source": "reported"})
    new = len(tr["apps"]) > before
    tracker.save(APP_DIR, tr)
    counter = None
    if new:
        d = daily.load_daily()
        it = _applications_counter(d)
        if it is not None:
            _, cnt = daily.daily_state(d, it, date)
            daily.daily_set(d, it, date, count=cnt + 1)
            daily.save_daily(d)
            _, cnt = daily.daily_state(d, it, date)
            counter = {"title": it["title"], "count": cnt, "target": it.get("target")}
    return {"app_id": app["id"], "new": new, "company": tracker.app_company(app), "role": tracker.app_role(app),
            "daily": counter}
