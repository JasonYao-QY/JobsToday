# -*- coding: utf-8 -*-
"""Reading the text/calendar part of a mail message. Leaf module: text in, event dicts out.

A calendar invitation carries what an announcement only implies — exact start, title, location,
organizer — so an invite needs no guessing, unlike the prose that extract_due has to cope with.

Times come back as local-time strings in the app's usual shapes: "YYYY-MM-DD HH:MM", or "YYYY-MM-DD"
for an all-day event. A UTC stamp or a TZID is converted; a floating time is taken as local.

Public: parse
"""

import datetime as dt
import re

__all__ = ["parse"]

_UNFOLD = re.compile(r"\r?\n[ \t]")
_ESCAPES = (("\\\\", "\x00"), ("\\n", "\n"), ("\\N", "\n"), ("\\,", ","), ("\\;", ";"))


def _unescape(v):
    for a, b in _ESCAPES:
        v = v.replace(a, b)
    return v.replace("\x00", "\\")


def _split_line(line):
    """'DTSTART;TZID=America/New_York:20261005T150000' -> ('DTSTART', {'TZID': '...'}, '2026...')"""
    i, quoted = 0, False
    while i < len(line):
        c = line[i]
        if c == '"':
            quoted = not quoted
        elif c == ":" and not quoted:
            break
        i += 1
    if i >= len(line):
        return "", {}, ""
    head, value = line[:i], line[i + 1:]
    bits = head.split(";")
    name = bits[0].strip().upper()
    params = {}
    for b in bits[1:]:
        k, _, v = b.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return name, params, value


def _zone(tzid):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzid)
    except Exception:
        return None


def _stamp(value, params):
    """-> (text, all_day). Unparseable input gives ('', False) rather than raising."""
    v = (value or "").strip()
    if not v:
        return "", False
    if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and "T" not in v):
        try:
            return dt.datetime.strptime(v[:8], "%Y%m%d").date().isoformat(), True
        except ValueError:
            return "", False
    utc = v.endswith("Z")
    try:
        naive = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return "", False
    if utc:
        local = naive.replace(tzinfo=dt.timezone.utc).astimezone()
        return local.strftime("%Y-%m-%d %H:%M"), False
    zone = _zone(params["TZID"]) if params.get("TZID") else None
    if zone is not None:
        local = naive.replace(tzinfo=zone).astimezone()
        return local.strftime("%Y-%m-%d %H:%M"), False
    return naive.strftime("%Y-%m-%d %H:%M"), False     # floating: already local by definition


def parse(text):
    """-> [{uid, summary, start, end, all_day, location, organizer, method, cancelled}], newest first
    in file order. An event with no usable DTSTART is dropped; a malformed calendar gives []."""
    if not text or "BEGIN:VEVENT" not in text.upper():
        return []
    body = _UNFOLD.sub("", text)
    method, out, cur = "", [], None
    for raw in body.splitlines():
        name, params, value = _split_line(raw.strip())
        if not name:
            continue
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            cur = {"uid": "", "summary": "", "start": "", "end": "", "all_day": False,
                   "location": "", "organizer": "", "status": ""}
            continue
        if name == "END" and value.strip().upper() == "VEVENT":
            if cur and cur["start"]:
                cur["method"] = method
                cur["cancelled"] = method == "CANCEL" or cur.pop("status", "") == "CANCELLED"
                cur.pop("status", None)
                out.append(cur)
            cur = None
            continue
        if cur is None:
            if name == "METHOD":
                method = value.strip().upper()
            continue
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _stamp(value, params)
        elif name == "DTEND":
            cur["end"], _ = _stamp(value, params)
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value).strip()
        elif name == "LOCATION":
            cur["location"] = _unescape(value).strip()
        elif name == "UID":
            cur["uid"] = value.strip()
        elif name == "STATUS":
            cur["status"] = value.strip().upper()
        elif name == "ORGANIZER":
            cur["organizer"] = (params.get("CN") or re.sub(r"(?i)^mailto:", "", value.strip())).strip()
    return out
