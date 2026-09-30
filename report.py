# -*- coding: utf-8 -*-
"""Reporting an application yourself ("I applied to Acme, SWE Intern"), for the CLI and the window alike.

It touches two slices through their public functions: the application tracker gets an "applied" event with
source "reported", and the daily counter that counts applications goes up by one, once per application.

"Once per application" is recorded on the application itself (counted_on), not inferred from whether the
report created the record: the mail scan often finds the confirmation first, and that application still
has to count when you report it. A confirmation email that arrives later joins the same record through the
tracker's own matching (same company, similar role), so nothing is counted twice. Reporting the same thing
twice on one day is a no-op (the event key is company + role + date).

Public: report_applied(company, role, date=None, link="")
"""

import re

import daily
import tracker
from store import APP_DIR
from timeutil import today_str


def _applications_counter(d):
    """The daily item that counts applications: the one marked {"counts": "applications"}, else the first counter
    whose title talks about applying ("Apply for 20 simplifyjob positions"), which then gets the marker so that
    renaming it later doesn't stop the counting."""
    for it in d["items"]:
        if it.get("counts") == "applications":
            return it
    for it in d["items"]:
        if it.get("target") and re.search(r"\bappl(?:y|ied|ying|ications?)\b", it.get("title", ""), re.I):
            it["counts"] = "applications"
            return it
    return None


def _report_key(company, role, date):
    return "reported:%s:%s:%s" % (tracker.company_key(company), "-".join(sorted(tracker.role_tokens(role))), date)


def report_applied(company, role, date=None, link=""):
    """-> {"app_id", "new", "counted", "company", "role", "daily": {"title", "count", "target"} or None}
    new: the report created the tracker record. counted: this report is what counted the application."""
    company, role = (company or "").strip(), (role or "").strip()
    if not company:
        raise ValueError("which company?")
    date = date or today_str()
    tr = tracker.load(APP_DIR)
    key = _report_key(company, role, date)
    if tr["seen"].get(key) == "deleted":
        del tr["seen"][key]                  # an explicit new report brings a deleted application back
    before = len(tr["apps"])
    ev = {"key": key, "date": date, "kind": "applied", "company": company, "role": role,
          "subject": "Reported: applied", "link": link, "account": "", "platform": "", "source": "reported"}
    app = tracker.ingest(tr, ev)
    if app is None:                          # the same report again: use the record it went to
        app = tracker.find(tr, tr["seen"].get(key))
        if app is None:                      # that record is gone (merged away oddly): report afresh
            del tr["seen"][key]
            app = tracker.ingest(tr, ev)
    new = len(tr["apps"]) > before
    counted = not app.get("counted_on")
    if counted:
        app["counted_on"] = date
    tracker.save(APP_DIR, tr)
    counter = None
    if counted:
        d = daily.load_daily()
        it = _applications_counter(d)
        if it is not None:
            _, cnt = daily.daily_state(d, it, date)
            daily.daily_set(d, it, date, count=cnt + 1)
            daily.save_daily(d)
            _, cnt = daily.daily_state(d, it, date)
            counter = {"title": it["title"], "count": cnt, "target": it.get("target")}
    return {"app_id": app["id"], "new": new, "counted": counted, "company": tracker.app_company(app),
            "role": tracker.app_role(app), "daily": counter}
