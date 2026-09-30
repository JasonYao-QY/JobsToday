# -*- coding: utf-8 -*-
"""Application tracker for `today`.

Turns classified job mail (confirmations, assessments, interviews, offers, rejections) into one record per
application and keeps it forever in applications.json (the inbox only keeps 90 days).

Record = company + role + the emails that belong to it. Stage and status are derived from those emails every
time they're read, unless you override them by hand, so nothing goes stale:

  stage   applied → oa (assessment) → oa_done (assessment submitted) → interview → offer
  status  active | rejected | withdrawn | closed      (rejected comes from mail; the others are yours)

Matching an email to an application: same company (tolerant of "JPMorganChase" / "JPMorgan Ridgeline", "LTC" /
"Lakeside Trading Company (LTC)", "TRN" / "Hudson River Trading"), then the most similar role. When a company
has several applications and the email doesn't name the role, it's attached to the likeliest one and flagged
("uncertain") so you can move it.
"""

import datetime as dt
import json
import os
import re
import time
import uuid

STAGES = ["applied", "oa", "oa_done", "interview", "offer"]
STAGE_LABEL = {"applied": "Applied", "oa": "Assessment", "oa_done": "Assessment done",
               "interview": "Interview", "offer": "Offer"}
STATUSES = ["active", "rejected", "withdrawn", "closed"]
STATUS_LABEL = {"active": "Active", "rejected": "Rejected", "withdrawn": "Withdrawn", "closed": "Closed"}
EVENT_KINDS = {"applied", "oa", "oa_done", "interview", "offer", "rejection"}
# where an event came from: an email the scan read, the hand-seeded first inbox, or you ("I applied to X")
SOURCES = ("mail", "seed", "reported")
EVENT_LABEL = {"applied": "Applied", "oa": "Assessment", "oa_done": "Assessment done", "interview": "Interview",
               "offer": "Offer", "rejection": "Rejected"}
DEFAULT_HISTORY_SINCE = "2026-06-01"


# ----------------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------------

def path(app_dir):
    return os.path.join(app_dir, "applications.json")


def load(app_dir):
    try:
        with open(path(app_dir), "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("apps", [])
    d.setdefault("seen", {})       # message key -> app id ("deleted" once its application was deleted)
    d.setdefault("history", {})    # account -> {"since": date, "done": time}
    return d


def save(app_dir, d, today=None):
    d["totals"] = totals(d, today)          # stored too, so the file alone answers "how many?"
    d["updated"] = time.strftime("%Y-%m-%d %H:%M")
    p = path(app_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)


def new_id():
    return uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------------
# role extraction ("received your application for the ___ position")
# ----------------------------------------------------------------------------

ROLE_WORD = re.compile(r"\b(intern|interns|internship|internships|co-?op|engineer|engineering|developer|development|"
                       r"scientist|science|analyst|quant|quantitative|research|researcher|program|programme|"
                       r"associate|fellow|fellowship|apprentice|apprenticeship|trader|designer|swe|sde|technologist)\b",
                       re.I)
CUES = [
    r"application (?:for|to) (?:the |our |a |an )?(?:position(?: of)? |role(?: of)? )?",
    r"(?:applying|apply|applied) (?:to|for) (?:the |our |a |an )?(?:position(?: of)? |role(?: of)? )?",
    r"interest in (?:the |our |a |an )?(?:role of |position of |position |role )?",
    r"(?:submission|submitted|resume/cv|resume|cv|candidacy) (?:to|for) (?:the |our )?(?:following position: ?)?",
    r"reviewed your (?:application|submission|candidacy) for (?:the |our )?",
    r"(?:process|stage|step) for (?:the |our )?",
    r"(?:get started in|considered for|consideration for|invite you to (?:interview|apply) for) (?:the |our )?",
    r"(?:regarding|re:?) (?:the |your )?",
]
CUE_RE = re.compile("(?:" + "|".join(CUES) + ")", re.I)
# where a role phrase stops
STOP_RE = re.compile(
    r"(?:\s+(?:position|role|opening|job|requisition|posting)\b"
    r"|\s+(?:at|with|here at)\s+(?=[A-Z0-9])"
    r"|[!?\n]|\.\s|\.$|\s\.|\s,"
    r"|,?\s+and\s+(?:for|we|are|will|is|would|appreciate|have|our|it|the)\b"
    r"|\s+(?:is in|has been|was|were|we|our|after|at this|unfortunately|please|you|as the|as a|as part|this|thank|"
    r"if|in the|due to|a member|and we)\b"
    r"|\s+-\s+(?:alex|alex)\b|\s+\|)", re.I)
BAD_START = re.compile(r"^(?:joining|working|a career|careers?|opportunit|our (?:organization|company|team)|being|"
                       r"your|this|that|us|it|them|future|other|employment|learning)\b", re.I)
REQ_ID = re.compile(r"(?:^|\s)(?:[A-Z]{1,4}-?\d{4,}|R-?\d{4,}|\d{4}-\d{3,}|\d{5,})\s*[-–—:]?\s*")
PAREN_ID = re.compile(r"\s*\((?:ID:?\s*)?(?:[A-Z]{0,4}-?)?\d{5,}\)|\s*,\s*\d{5,}\b|\s*\[not available\]", re.I)


def clean_role(s):
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = PAREN_ID.sub("", s)
    s = REQ_ID.sub(" ", " " + s).strip()
    s = re.sub(r"\s+([,.)\]])", r"\1", s)
    s = re.sub(r"[\[\]]", "", s)
    s = re.sub(r"^(?:(?:the|our|a|an|for|to|of)\s+)+", "", s, flags=re.I)
    s = s.strip(" -–—:,.;\"'“”")
    if s.count("(") > s.count(")"):
        s = s[: s.rfind("(")].strip(" -–—:,")
    return s


def tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower())]


def looks_like_role(s, company=""):
    if not s or len(s) < 4 or len(s) > 140:
        return False
    if BAD_START.match(s) or "@" in s or "http" in s.lower():
        return False
    ctoks = set(tokens(company)) - {"the"}
    rest = " ".join(t for t in tokens(s) if t not in ctoks)
    return bool(ROLE_WORD.search(rest))


def extract_role(subject, body, company=""):
    """-> role string or ''. Body first (it's where ATSs put 'the X position'), then the subject."""
    for text in ((body or "")[:1500], subject or ""):
        text = re.sub(r"[ \t\r]+", " ", text.replace("&amp;", "&").replace("&#39;", "'"))
        for m in CUE_RE.finditer(text):
            rest = text[m.end(): m.end() + 170]
            stop = STOP_RE.search(rest)
            cand = clean_role(rest[: stop.start()] if stop else rest)
            if looks_like_role(cand, company):
                return cand[:110]
    # subject shapes like "Your Application with Dover Scientific - R&D Software Engineering Intern (634871)"
    for part in re.split(r"\s+[-–—|:]\s+", subject or ""):
        cand = clean_role(part)
        if looks_like_role(cand, company) and not re.search(
                r"\b(?:applications?|thanks?|update|status|your|assessments?|tests?|challenge|survey|webinar|events?|"
                r"reminder|invit\w*|complete|completing|taking|submission|confirmation|next steps?)\b", cand, re.I):
            return cand[:110]
    return ""


# ----------------------------------------------------------------------------
# matching
# ----------------------------------------------------------------------------

COMPANY_DROP = {"the", "inc", "llc", "ltd", "corp", "corporation", "co", "company", "group", "holdings", "plc", "lp",
                "llp", "and", "&", "of"}
ROLE_DROP = {"summer", "winter", "spring", "fall", "autumn", "intern", "interns", "internship", "internships",
             "program", "programme", "the", "a", "an", "of", "and", "in", "for", "to", "at", "position", "role",
             "opening", "us", "usa", "co", "op", "coop", "student", "students", "university", "grad", "graduate",
             "multiple", "locations", "hybrid", "remote", "onsite"}
STEM = {"engineering": "engineer", "engineers": "engineer", "developer": "develop", "development": "develop",
        "developers": "develop", "scientist": "science", "scientists": "science", "sciences": "science",
        "researcher": "research", "quantitative": "quant", "analysts": "analyst", "analytics": "analyst",
        "trader": "trading", "traders": "trading", "sde": "develop", "swe": "software"}


def company_key(s):
    return re.sub(r"[^a-z0-9]", "", " ".join(t for t in tokens(s) if t not in COMPANY_DROP))


def same_company(a, b):
    if not a or not b:
        return False
    ka, kb = company_key(a), company_key(b)
    if ka and ka == kb:
        return True
    ta = [t for t in tokens(a) if t not in COMPANY_DROP]
    tb = [t for t in tokens(b) if t not in COMPANY_DROP]
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    if sa <= sb or sb <= sa:                       # "LTC" vs "Lakeside Trading Company (LTC)"
        return True
    fa = [t for t in tokens(a) if t not in ("the", "and", "of")]
    fb = [t for t in tokens(b) if t not in ("the", "and", "of")]
    for short, long_ in ((ka, fb), (kb, fa)):      # "TRN" vs "Hudson River Trading", "LTC" vs "Lakeside Trading Company"
        if 3 <= len(short) <= 5 and len(long_) == len(short) and "".join(t[0] for t in long_) == short:
            return True
    return False


def role_tokens(s):
    out = set()
    for t in tokens(s):
        if t in ROLE_DROP or re.fullmatch(r"\d{2,}", t):
            continue
        out.add(STEM.get(t, t))
    return out


def role_sim(a, b):
    """0..1; 0 when either side is unknown."""
    A, B = role_tokens(a), role_tokens(b)
    if not A or not B:
        return 0.0
    j = len(A & B) / float(len(A | B))
    if A <= B or B <= A:
        j = max(j, 0.8)
    return j


def app_company(a):
    return (a.get("manual") or {}).get("company") or a.get("company", "")


def app_role(a):
    return (a.get("manual") or {}).get("role") or a.get("role", "")


def days_between(d1, d2):
    try:
        return abs((dt.date.fromisoformat(d1[:10]) - dt.date.fromisoformat(d2[:10])).days)
    except (TypeError, ValueError):
        return 9999


def first_date(a):
    ds = [e["date"] for e in a.get("events", []) if e.get("date")]
    return min(ds) if ds else a.get("created", "")


def last_date(a):
    ds = [e["date"] for e in a.get("events", []) if e.get("date")]
    return max(ds) if ds else a.get("created", "")


def match(cands, ev):
    """-> (app or None, uncertain)"""
    if not cands:
        return None, False
    role, kind, date = ev.get("role", ""), ev["kind"], ev.get("date", "")
    if kind == "applied":
        if role:
            best = max(cands, key=lambda a: role_sim(role, app_role(a)))
            if role_sim(role, app_role(best)) >= 0.75:
                return best, False           # second confirmation for the same role (ATS + company, "complete your application")
            near = [a for a in cands if not app_role(a) and days_between(first_date(a), date) <= 2]
            if near:
                return near[-1], False       # a role-less confirmation from the same days: same application
            return None, False
        near = [a for a in cands if days_between(first_date(a), date) <= 2]
        return (near[-1], False) if near else (None, False)

    # progress or rejection: prefer applications still open, and ones that existed by then
    pool = [a for a in cands if derive(a)["status"] == "active"] or cands
    before = [a for a in pool if first_date(a) <= date] or pool
    if kind == "oa_done":
        waiting = [a for a in before if derive(a)["stage"] == "oa"]
        before = waiting or before
    if role:
        scored = sorted(before, key=lambda a: (role_sim(role, app_role(a)), last_date(a)))
        best = scored[-1]
        s = role_sim(role, app_role(best))
        if s >= 0.5:
            return best, False
        if len(before) == 1:
            return best, bool(app_role(best))   # role disagrees with the only candidate: attach, but flag it
        return best, True
    if len(before) == 1:
        return before[0], False
    return sorted(before, key=last_date)[-1], True


def new_app(ev):
    return {"id": new_id(), "company": ev.get("company") or "?", "role": ev.get("role", ""),
            "created": ev.get("date") or time.strftime("%Y-%m-%d"), "events": [], "manual": {}, "note": ""}


def ingest(d, ev):
    """Attach one classified email to an application (creating it if needed). Idempotent by ev['key']."""
    if not ev.get("key") or ev.get("kind") not in EVENT_KINDS or ev["key"] in d["seen"]:
        return None
    cands = [a for a in d["apps"] if same_company(app_company(a), ev.get("company", ""))]
    target, uncertain = match(cands, ev)
    if target is None:
        target = new_app(ev)
        d["apps"].append(target)
    elif ev.get("role") and not target.get("role"):
        target["role"] = ev["role"]
    ev = dict(ev)
    ev["uncertain"] = bool(uncertain)
    target["events"].append(ev)
    target["events"].sort(key=lambda e: (e.get("date", ""), STAGES.index(e["kind"]) if e["kind"] in STAGES else 9))
    d["seen"][ev["key"]] = target["id"]
    return target


def event_from_item(item):
    """An inbox item (today.py's mail scan, or the hand-seeded ones) -> tracker event, or None."""
    kind = item.get("kind")
    if kind == "oa" and item.get("status") == "done" and item.get("source") == "seed":
        kind = "oa_done"
    if kind not in EVENT_KINDS or not item.get("key"):
        return None
    title = item.get("title", "")
    role = item.get("role")
    if role is None:  # items from before the tracker existed
        role = title if (item.get("source") == "seed" and kind in ("applied", "rejection")
                         and looks_like_role(title, item.get("company", ""))) else extract_role(title, "", item.get("company", ""))
    return {"key": item["key"], "date": item.get("date", ""), "kind": kind, "company": item.get("company", ""),
            "role": role or "", "subject": title, "link": item.get("link", ""), "account": item.get("account", ""),
            "platform": item.get("platform", ""), "source": item.get("source") or "mail"}


def ingest_items(d, items):
    n = 0
    for it in sorted(items, key=lambda i: (i.get("date", ""), 0 if i.get("kind") == "applied" else 1)):
        ev = event_from_item(it)
        if ev and ingest(d, ev):
            n += 1
    return n


def drop_seed_events(d, accounts):
    """Once an account's real history is in, the hand-seeded copies of its emails go (like the inbox does)."""
    accounts = set(accounts)
    keep = []
    for a in d["apps"]:
        a["events"] = [e for e in a["events"] if not (e.get("source") == "seed" and e.get("account") in accounts)]
        if a["events"] or a.get("manual") or a.get("note"):
            keep.append(a)
    d["apps"] = keep


# ----------------------------------------------------------------------------
# derived view
# ----------------------------------------------------------------------------

def derive(a, today=None):
    evs = a.get("events", [])
    man = a.get("manual") or {}
    auto_stage = "applied"
    stage_date = ""
    for e in evs:
        k = e.get("kind")
        if k in STAGES and STAGES.index(k) >= STAGES.index(auto_stage):
            if k != auto_stage or not stage_date:
                stage_date = e.get("date", "")
            auto_stage = k
    rejections = [e for e in evs if e.get("kind") == "rejection"]
    auto_status = "rejected" if rejections else "active"
    stage = man.get("stage") or auto_stage
    status = man.get("status") or auto_status
    applied_evs = [e for e in evs if e.get("kind") == "applied"]
    applied = applied_evs[0]["date"] if applied_evs else first_date(a)
    last = last_date(a)
    quiet = None
    if status == "active" and last:
        quiet = days_between(last, today or time.strftime("%Y-%m-%d"))
    return {
        "id": a["id"], "company": app_company(a), "role": app_role(a), "note": a.get("note", ""),
        "stage": stage, "stage_label": STAGE_LABEL.get(stage, stage), "auto_stage": auto_stage,
        "status": status, "status_label": STATUS_LABEL.get(status, status), "auto_status": auto_status,
        "stage_manual": bool(man.get("stage")), "status_manual": bool(man.get("status")),
        "stage_date": stage_date, "applied": applied, "last": last, "quiet_days": quiet,
        "ended": (rejections[-1]["date"] if rejections else "") if status == "rejected" else "",
        "confirmed": bool(applied_evs), "uncertain": any(e.get("uncertain") for e in evs),
        "events": [{"key": e["key"], "date": e.get("date", ""), "kind": e.get("kind"),
                    "label": EVENT_LABEL.get(e.get("kind"), e.get("kind")), "subject": e.get("subject", ""),
                    "link": e.get("link", ""), "uncertain": bool(e.get("uncertain")), "platform": e.get("platform", ""),
                    "source": e.get("source", "mail")} for e in evs],
    }


def totals(d, today=None):
    rows = [derive(a, today) for a in d["apps"]]
    t = {"applications": len(rows), "rejected": 0, "active": 0, "withdrawn": 0, "closed": 0,
         "waiting": 0, "in_progress": 0, "reached_oa": 0, "reached_interview": 0, "offers": 0,
         "rejection_emails": 0, "heard_back": 0}
    for r in rows:
        t[r["status"]] = t.get(r["status"], 0) + 1
        idx = STAGES.index(r["stage"]) if r["stage"] in STAGES else 0
        if r["status"] == "active":
            if idx == 0:
                t["waiting"] += 1
            else:
                t["in_progress"] += 1
        if idx >= 1:
            t["reached_oa"] += 1
        if idx >= 3:
            t["reached_interview"] += 1
        if idx >= 4:
            t["offers"] += 1
        if idx >= 1 or r["status"] == "rejected":
            t["heard_back"] += 1
        t["rejection_emails"] += sum(1 for e in r["events"] if e["kind"] == "rejection")
    return t


def weekly(d, weeks=12, today=None):
    """Applications (by applied date) and rejections (by date) per week, oldest first."""
    today = dt.date.fromisoformat(today) if today else dt.date.today()
    start = today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=weeks - 1)
    out = [{"week": (start + dt.timedelta(weeks=i)).isoformat(), "applied": 0, "rejected": 0} for i in range(weeks)]

    def bucket(date):
        try:
            k = (dt.date.fromisoformat(date[:10]) - start).days // 7
        except (TypeError, ValueError):
            return None
        return k if 0 <= k < weeks else None

    for a in d["apps"]:
        r = derive(a)
        k = bucket(r["applied"])
        if k is not None:
            out[k]["applied"] += 1
        if r["status"] == "rejected":
            k = bucket(r["ended"])
            if k is not None:
                out[k]["rejected"] += 1
    return out


# ----------------------------------------------------------------------------
# edits by hand
# ----------------------------------------------------------------------------

def find(d, app_id):
    for a in d["apps"]:
        if a["id"] == app_id:
            return a
    return None


def update(d, app_id, fields):
    a = find(d, app_id)
    if a is None:
        raise KeyError(app_id)
    man = a.setdefault("manual", {})
    for k in ("stage", "status"):
        if k in fields:
            v = fields[k] or None
            if v and v not in (STAGES if k == "stage" else STATUSES):
                raise ValueError("unknown %s %r" % (k, v))
            if v:
                man[k] = v
            else:
                man.pop(k, None)
    for k in ("company", "role"):
        if k in fields:
            v = (fields[k] or "").strip()
            if v and v != a.get(k, ""):
                man[k] = v
            else:
                man.pop(k, None)
    if "note" in fields:
        a["note"] = (fields["note"] or "").strip()
    return a


def delete(d, app_id):
    a = find(d, app_id)
    if a is None:
        return
    for e in a["events"]:
        d["seen"][e["key"]] = "deleted"   # so a rescan doesn't bring it back
    d["apps"] = [x for x in d["apps"] if x["id"] != app_id]


def move_event(d, key, target_id):
    """Move one email to another application ('new' = its own application)."""
    src = next((a for a in d["apps"] if any(e["key"] == key for e in a["events"])), None)
    if src is None:
        raise KeyError(key)
    ev = next(e for e in src["events"] if e["key"] == key)
    src["events"] = [e for e in src["events"] if e["key"] != key]
    if target_id == "new":
        dst = new_app(ev)
        dst["company"] = app_company(src)
        d["apps"].append(dst)
    else:
        dst = find(d, target_id)
        if dst is None:
            raise KeyError(target_id)
    ev["uncertain"] = False
    ev["moved"] = True
    dst["events"].append(ev)
    dst["events"].sort(key=lambda e: e.get("date", ""))
    d["seen"][key] = dst["id"]
    if not src["events"] and not src.get("manual") and not src.get("note"):
        d["apps"] = [x for x in d["apps"] if x["id"] != src["id"]]
    return dst


def merge(d, src_id, dst_id):
    src, dst = find(d, src_id), find(d, dst_id)
    if src is None or dst is None or src is dst:
        raise KeyError(src_id)
    for e in src["events"]:
        e["uncertain"] = False
        dst["events"].append(e)
        d["seen"][e["key"]] = dst["id"]
    dst["events"].sort(key=lambda e: e.get("date", ""))
    if not dst.get("role") and src.get("role"):
        dst["role"] = src["role"]
    d["apps"] = [x for x in d["apps"] if x["id"] != src_id]
    return dst
