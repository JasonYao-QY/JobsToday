# -*- coding: utf-8 -*-
"""Deadlines: the ones you add (deadlines.json), merged with open assessments from the inbox, with countdowns.

Public: load_deadlines, save_deadlines, all_deadlines(deadlines, inbox), deadline_when (and DEADLINES_FILE).
The inbox is read only through inbox.assessment_deadlines().
"""

import os

from inbox import assessment_deadlines
from store import APP_DIR, load_json, save_json
from timeutil import fmt_when, short_date, until

__all__ = ["DEADLINES_FILE", "load_deadlines", "save_deadlines", "all_deadlines", "deadline_when"]


DEADLINES_FILE = os.path.join(APP_DIR, "deadlines.json")


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
    out += assessment_deadlines(inbox)
    for d in out:
        if d["done"]:
            d["until"], d["sev"] = "done", "done"
        else:
            d["until"], d["sev"] = until(d["at"])
    out.sort(key=lambda d: (2, "", "") if d["done"] else (0 if d["at"] else 1, d["at"] or "", d["name"]))
    return out


def deadline_when(d):
    if d["done"]:
        return ("submitted " + short_date(d["date"])) if d.get("date") else "done"
    return fmt_when(d["at"]) if d["at"] else "rolling"
