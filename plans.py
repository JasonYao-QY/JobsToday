# -*- coding: utf-8 -*-
"""Day plans: one file per day in plans/YYYY-MM-DD.json, and carrying unfinished items forward.

Public: load_plan, save_plan, add_item, sorted_items, plan_dates, carry_candidates, carry_over (and PLANS_DIR).
A plan is {"date", "items": [{"id", "title", "time" ("HH:MM" or ""), "note", "mins", "urgent", "done", "created"}]}.
"""

import os
import time

from store import APP_DIR, load_json, new_id, save_json

__all__ = ["PLANS_DIR", "load_plan", "save_plan", "add_item", "sorted_items", "plan_dates", "carry_candidates",
           "carry_over", "plan_path"]


PLANS_DIR = os.path.join(APP_DIR, "plans")


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
