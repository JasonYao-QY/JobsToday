# -*- coding: utf-8 -*-
"""Daily recurring tasks: checkboxes and counters ("Apply for 20 jobs" → 0/20), logged per day in daily.json.

Public: load_daily, save_daily (keeps 90 days of log), guess_target, daily_state, daily_set (and DAILY_FILE).
daily.json is {"items": [{"id", "title", "target" (int or null)}], "log": {date: {item id: {"done", "count"}}}}.
"""

import datetime as dt
import os
import re

from store import APP_DIR, load_json, save_json

__all__ = ["DAILY_FILE", "load_daily", "save_daily", "guess_target", "daily_state", "daily_set"]


DAILY_FILE = os.path.join(APP_DIR, "daily.json")


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
