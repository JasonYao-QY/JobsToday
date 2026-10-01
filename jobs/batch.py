# -*- coding: utf-8 -*-
"""Assemble a batch, record what was shown, and turn "applied through 13" into tracker records.

The only module in jobs/ that talks to the rest of the app (tracker, report, env, store), and the only one
the Api calls. Everything it composes is a leaf with its own tests; nothing here ranks, filters or parses.
Synchronous: prepare() fetches, so the caller (the Api, step 7) runs it in a BackgroundTask.

Rules this module exists to keep:

  An open batch carries over. He works down the list in order and reports "applied through 13". Rows 14-20
  are unfinished, not rejected, but they are already "shown", so a naive next batch would drop them. The
  next batch therefore starts with the open batch's unreported rows, in their original order, neither
  re-ranked nor re-filtered. A row is retired only when he says so (skip / close_batch).

  The watermark never moves. It is the one-time backlog cutoff, set on first run.

  Never pad. Supply is thinner than his 20-a-day target, so a short batch is the normal case; prepare()
  returns the number asked for and the number found and leaves the wording to the tab.

  Verification is lazy. A batch is built with every verdict unknown; one row is verified when he opens it.

  The LLM key (TODAY_LLM_KEY in .env) is read here, handed to verify.LLMVerifier, and never stored, logged
  or returned. With no key the batch still works and says it is unverified.

Public: connect, bootstrap, status, unreported_count, prepare, open_batch, report_through, skip, close_batch,
verify_row, why_dropped
"""

import datetime
import os
import time
import urllib.error

import env
import report
import tracker
from store import APP_DIR

from jobs import filters, listings, rank, state, verify

__all__ = [
    "DATA_DIR",
    "connect",
    "bootstrap",
    "status",
    "unreported_count",
    "prepare",
    "open_batch",
    "report_through",
    "skip",
    "close_batch",
    "verify_row",
    "why_dropped",
]

# Data, not source: the install keeps source and data in one folder, so the slice's data gets its own
# directory rather than living next to these modules.
DATA_DIR = os.path.join(APP_DIR, "jobs-data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DB_PATH = os.path.join(DATA_DIR, "jobs.db")
SEED_PATH = os.path.join(DATA_DIR, "exclusions.seed.json")

# He closed out his earlier screening batches through this date (owner decision, 2026-10-01).
DEFAULT_CUTOFF = "2026-09-24"

KEY_NAME = "TODAY_LLM_KEY"

# The seed is a point-in-time copy of a list kept outside the app. Past this age the tab says so.
SEED_STALE_DAYS = 21

# What Api.version() stats to notice a change. In WAL mode a write lands in the -wal file first and reaches
# the main file only at a checkpoint, so the main file's mtime alone can miss it.
WATCH_PATHS = (DB_PATH, DB_PATH + "-wal")

# tracker.role_sim is a 0..1 similarity, not a yes/no. This is the threshold tracker.match itself uses to
# call two "applied" events the same role, so a listing is excluded exactly when a confirmation email for
# it would have joined the existing application.
SAME_ROLE = 0.75


# ----------------------------------------------------------------------------
# the tracker's matchers, adapted to what filters and listings expect
# ----------------------------------------------------------------------------

def _company_key(name):
    """tracker.company_key, except that a name made only of dropped words ("The Company") must not collapse
    to the empty key and merge with every other such name."""
    return tracker.company_key(name) or " ".join((name or "").casefold().split())


def _same_role(a, b):
    return tracker.role_sim(a, b) >= SAME_ROLE


def _applications():
    """applications.json as exclusion input. A record with no role closes the whole company: the mail did
    not say which role, so no listing there can be told apart from it."""
    out = []
    for a in tracker.load(APP_DIR)["apps"]:
        company = tracker.app_company(a)
        if not company or company == "?":
            continue
        out.append({"company": company, "role": tracker.app_role(a) or None,
                    "status": tracker.derive(a)["status"]})
    return out


# ----------------------------------------------------------------------------
# setup
# ----------------------------------------------------------------------------

def connect():
    return state.open_db(DB_PATH)


def _cutoff_epoch(date):
    """The last second of `date`, UTC. The feed's post dates are UTC midnights, so the comparison is made in
    UTC: a posting dated the day after the cutoff is new wherever this runs."""
    d = datetime.date.fromisoformat(date)
    end = datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=datetime.timezone.utc)
    return int(end.timestamp())


def bootstrap(conn, cutoff=None, force=False):
    """Set the backlog cutoff and import the exclusion seed.

    The cutoff is set once: when none is set yet, or when force is True (he changed his mind about the
    date). Without it watermark() is 0, which means no protection, and the first batch would offer the
    whole backlog. The seed import is additive and idempotent, so it runs every time.

    -> {"cutoff": epoch, "cutoff_set": bool, "seed_entries": n}
    """
    was = state.watermark(conn)
    set_now = force or was == 0
    if set_now:
        state.set_watermark(conn, _cutoff_epoch(cutoff or DEFAULT_CUTOFF))
    n = state.import_exclusions(conn, SEED_PATH)
    return {"cutoff": state.watermark(conn), "cutoff_set": set_now, "seed_entries": n}


def _seed_age(conn, now):
    taken = state.seed_info(conn).get("taken")
    if not taken:
        return None
    try:
        d = datetime.date.fromisoformat(taken)
    except ValueError:
        return None
    today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    return {"taken": taken, "days": max(0, (today - d).days)}


# ----------------------------------------------------------------------------
# prepare
# ----------------------------------------------------------------------------

def _load_listings(fetch, opener):
    """(listings, feed status). A failed fetch is reported, not raised: the cached feed is still good."""
    feed = {"fetched": False, "changed": False, "error": None}
    if fetch:
        for name in listings.FEEDS:
            try:
                r = listings.fetch(name, CACHE_DIR, opener=opener)
                feed["fetched"] = True
                feed["changed"] = feed["changed"] or r["changed"]
            except (urllib.error.URLError, OSError, ValueError) as e:
                feed["error"] = "%s: %s" % (type(e).__name__, e)
    return listings.load(CACHE_DIR, company_key=_company_key), feed


def _row(entry):
    """A rank entry -> the row state.create_batch stores. `company` is the listing's full upstream string;
    report_applied gets exactly this, never a shortened display name."""
    return {
        "listing_key": entry["key"], "variant_id": entry["variant_id"] or "",
        "company": entry["company"], "title": entry["title"], "city": entry["city"],
        "url": entry["url"], "resume": entry["resume"],
        "detail": {"score": entry["score"], "parts": entry["parts"], "flags": entry["flags"],
                   "resume_why": entry["resume_why"], "track": entry["track"], "posted": entry["posted"]},
    }


def _carry(row):
    """An unreported row of the open batch, as it goes into the next one: untouched, apart from a note of
    which batch it came from, so the tab can say why it is at the top."""
    out = {k: row[k] for k in ("listing_key", "variant_id", "company", "title", "city", "url", "resume")}
    detail = dict(row["detail"] or {})
    detail.setdefault("carried_from", row["batch_id"])       # the batch it was first shown in
    out["detail"] = detail
    return out


def _unreported(conn, batch):
    if batch is None:
        return []
    return [r for r in state.batch_rows(conn, batch["id"]) if r["status"] == "shown"]


def _verifier_name():
    return "llm" if env.get(KEY_NAME) else "none"


def prepare(conn, n=20, fetch=True, now=None, opener=None):
    """Assemble the next batch.

    -> {"batch_id", "created", "rows", "carried", "added", "found", "requested", "short", "dropped",
        "feed", "seed_age", "verifier", "cutoff"}

    rows      every row of the current batch, with status, in position order
    carried   how many came over unreported from the previous batch (they are first, in their old order)
    found     eligible new postings found this time; added is how many of them fit under n
    short     fewer rows than requested. Not an error and never padded.
    dropped   the candidates the filters removed, each with its reason
    created   False when there was nothing new to add: the open batch (if any) is returned as it stands,
              so asking twice in a row does not mint a second identical batch
    """
    now = int(now if now is not None else time.time())
    n = max(1, int(n))
    if state.watermark(conn) == 0:
        bootstrap(conn)

    found_listings, feed = _load_listings(fetch, opener)
    state.sync_seen(conn, found_listings, now=now)

    exclusions = state.exclusion_union(conn, applications=_applications())
    candidates = state.new_since(conn, found_listings)
    ok, dropped = filters.check_all(candidates, exclusions, company_key=_company_key,
                                    role_match=_same_role, company_match=tracker.same_company)
    ordered = rank.rank(ok, company_key=_company_key, now=now)

    current = state.open_batch(conn)
    carried = _unreported(conn, current)
    fresh = ordered[: max(0, n - len(carried))]

    created = False
    if fresh:
        rows = [_carry(r) for r in carried] + [_row(e) for e in fresh]
        bid = state.create_batch(conn, rows, requested=n, now=now)
        if carried:
            # Only now does the old batch let go of them; done second so a crash in between duplicates
            # rows rather than losing them.
            state.mark(conn, current["id"], [r["position"] for r in carried], "carried")
        created = True
    else:
        bid = current["id"] if current else None

    out_rows = state.batch_rows(conn, bid) if bid is not None else []
    live = [r for r in out_rows if r["status"] == "shown"]
    state.save_summary(conn, {"at": now, "requested": n, "found": len(ordered), "added": len(fresh),
                              "carried": len(carried) if created else 0, "created": created,
                              "feed_error": feed["error"]})
    return {
        "batch_id": bid, "created": created, "rows": out_rows,
        "carried": len(carried) if created else 0,
        "added": len(fresh), "found": len(ordered), "requested": n, "short": len(live) < n,
        "dropped": [{"company": d["company"], "title": d["title"], "code": d["reasons"][0]["code"],
                     "text": d["reasons"][0]["text"]} for d in dropped],
        "feed": feed, "seed_age": _seed_age(conn, now), "verifier": _verifier_name(),
        "cutoff": state.watermark(conn),
    }


def _feed_info(now):
    info = {"cached": False, "fetched_at": None, "checked_at": None, "age_hours": None}
    for name in listings.FEEDS:
        c = listings.cache_info(name, CACHE_DIR)
        info["cached"] = info["cached"] or c["cached"]
        for k in ("fetched_at", "checked_at"):
            if c[k] and (info[k] is None or c[k] > info[k]):
                info[k] = c[k]
    if info["checked_at"]:
        try:
            t = datetime.datetime.fromisoformat(info["checked_at"])
            info["age_hours"] = max(0, int((now - t.timestamp()) // 3600))
        except ValueError:
            pass
    return info


def _empty_status(now):
    return {"ready": False, "batch_id": None, "rows": [], "unreported": [], "last_batch": None,
            "last_prepare": None, "feed": _feed_info(now), "seed_age": None, "seed_stale": False,
            "verifier": _verifier_name(), "key_name": KEY_NAME, "cutoff": None,
            "cutoff_date": DEFAULT_CUTOFF}


def status(now=None):
    """Everything the tab shows, read from disk. No fetch, no verification, and nothing is created: before
    the first prepare there is no database and this returns an empty payload the tab can still render.

    -> {"ready", "batch_id", "rows", "unreported", "last_batch", "last_prepare", "feed", "seed_age",
        "seed_stale", "verifier", "key_name", "cutoff", "cutoff_date"}
    """
    now = int(now if now is not None else time.time())
    if not os.path.exists(DB_PATH):
        return _empty_status(now)
    conn = connect()
    try:
        out = _empty_status(now)
        b = state.open_batch(conn)
        rows = state.batch_rows(conn, b["id"]) if b else []
        last = state.last_batch(conn)
        seed = _seed_age(conn, now)
        cutoff = state.watermark(conn)
        out.update(
            ready=True, batch_id=b["id"] if b else None, rows=rows,
            unreported=[r["position"] for r in rows if r["status"] == "shown"],
            last_batch=({"batch_id": last["id"], "created_at": last["created_at"],
                         "closed_at": last["closed_at"]} if last else None),
            last_prepare=state.load_summary(conn), seed_age=seed,
            seed_stale=bool(seed and seed["days"] > SEED_STALE_DAYS), cutoff=cutoff or None)
        if cutoff:
            out["cutoff_date"] = datetime.datetime.fromtimestamp(
                cutoff, datetime.timezone.utc).date().isoformat()
        return out
    finally:
        conn.close()


def unreported_count():
    """How many rows of the open batch are still waiting on him; 0 with no database. For the nav badge."""
    if not os.path.exists(DB_PATH):
        return 0
    conn = connect()
    try:
        b = state.open_batch(conn)
        return len(_unreported(conn, b)) if b else 0
    finally:
        conn.close()


def open_batch(conn):
    """The current unfinished batch -> {"batch_id", "rows", "unreported"} or None."""
    b = state.open_batch(conn)
    if b is None:
        return None
    rows = state.batch_rows(conn, b["id"])
    return {"batch_id": b["id"], "rows": rows,
            "unreported": [r["position"] for r in rows if r["status"] == "shown"]}


# ----------------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------------

def _require_open(conn):
    b = state.open_batch(conn)
    if b is None:
        raise ValueError("there is no open batch")
    return b


def report_through(conn, position):
    """ "Applied through 13": report every unreported row from 1 to `position` of the open batch.

    -> {"reported", "counted", "already_tracked", "daily", "remaining"}

    counted          applications this report is what counted (the daily counter went up by this much)
    already_tracked  rows whose tracker record already existed, e.g. the mail scan found the confirmation
                     first. Such a row still counts if nothing had counted it yet: counted is not "new".
    daily            the counter as of the last row that counted, or None if none did
    remaining        positions still unreported. They stay 'shown'; nothing is skipped on his behalf.
    """
    b = _require_open(conn)
    rows = state.batch_rows(conn, b["id"])
    position = int(position)
    if not 1 <= position <= len(rows):
        raise ValueError("position %d is outside this batch (1-%d)" % (position, len(rows)))

    done, app_ids = [], {}
    counted = already = 0
    daily = None
    try:
        for r in rows:
            if r["position"] > position or r["status"] != "shown":
                continue
            res = report.report_applied(r["company"], r["title"], link=r["url"] or "")
            done.append(r["position"])
            app_ids[r["position"]] = res["app_id"]
            counted += 1 if res["counted"] else 0
            already += 0 if res["new"] else 1
            if res["daily"] is not None:
                daily = res["daily"]
    finally:
        if done:                # keep what did get reported even if a later row raised
            state.mark(conn, b["id"], done, "applied", app_ids=app_ids)
    left = [r["position"] for r in state.batch_rows(conn, b["id"]) if r["status"] == "shown"]
    return {"reported": len(done), "counted": counted, "already_tracked": already, "daily": daily,
            "remaining": left}


def skip(conn, positions):
    """He passes on these rows. This, and only this, retires a listing he did not apply to."""
    b = _require_open(conn)
    live = {r["position"] for r in state.batch_rows(conn, b["id"]) if r["status"] == "shown"}
    todo = sorted(int(p) for p in positions if int(p) in live)
    state.mark(conn, b["id"], todo, "skipped")
    return {"skipped": todo}


def close_batch(conn):
    """Mark every unreported row of the open batch skipped. -> {"skipped": [positions]}; no-op if none."""
    b = state.open_batch(conn)
    if b is None:
        return {"skipped": []}
    return skip(conn, [r["position"] for r in _unreported(conn, b)])


# ----------------------------------------------------------------------------
# lazy verification
# ----------------------------------------------------------------------------

def verify_row(conn, position, fetch=None, transport=None):
    """Verify one row of the open batch, when he opens it. The verdict is stored on the row and returned.

    No key: the row is marked unverified and nothing is fetched or paid for. `fetch` and `transport` are
    injectable for tests.
    """
    b = _require_open(conn)
    row = next((r for r in state.batch_rows(conn, b["id"]) if r["position"] == int(position)), None)
    if row is None:
        raise ValueError("no position %s in this batch" % position)
    key = env.get(KEY_NAME)
    if not key:
        v = verify.NullVerifier().verify(row, "")
        v.update(summary="Not verified: no %s in .env." % KEY_NAME, page_hash=None, cached=False,
                 readable=None, error=None)
    else:
        verifier = verify.LLMVerifier(key, transport=transport)
        v = verify.verify(
            {"url": row["url"], "company": row["company"], "title": row["title"], "city": row["city"]},
            verifier,
            lambda h, name: state.get_verdict(conn, h, name),
            lambda h, name, verdict, url: state.put_verdict(conn, h, name, verdict, url=url),
            fetch=fetch)
    state.set_row_detail(conn, b["id"], row["position"], "verdict", v)
    return v


# ----------------------------------------------------------------------------
# "why isn't X here?"
# ----------------------------------------------------------------------------

def why_dropped(conn, company):
    """Every live posting at `company` and why it is or is not in front of him. Reads the cached feed; no
    network. -> list of {"company", "title", "status", "text", "reasons"}

    status: shown | excluded | backlog | waiting | not_in_feed
    """
    q = (company or "").strip()
    if not q:
        return []
    all_listings = listings.load(CACHE_DIR, company_key=_company_key)
    mine = [x for x in all_listings
            if tracker.same_company(q, x["company"]) or q.casefold() in x["company"].casefold()]
    if not mine:
        return [{"company": q, "title": "", "status": "not_in_feed", "reasons": [],
                 "text": "No live posting for %s in the feed." % q}]

    exclusions = state.exclusion_union(conn, applications=_applications())
    history = {}
    for h in state.shown_history(conn, [x["key"] for x in mine]):
        history[h["listing_key"]] = h            # the latest showing wins
    cutoff = state.watermark(conn)
    waiting = rank.rank(
        filters.check_all(state.new_since(conn, all_listings), exclusions, company_key=_company_key,
                          role_match=_same_role, company_match=tracker.same_company)[0],
        company_key=_company_key)
    place = {e["key"]: i for i, e in enumerate(waiting, 1)}

    out = []
    for x in mine:
        item = {"company": x["company"], "title": x["title"], "reasons": []}
        h = history.get(x["key"])
        if h is not None:
            word = {"shown": "is in batch %d at position %d, not yet reported",
                    "applied": "was in batch %d at position %d; you applied",
                    "skipped": "was in batch %d at position %d; you skipped it",
                    "carried": "was in batch %d at position %d and carried forward"}[h["status"]]
            item.update(status="shown", text=("It " + word + ".") % (h["batch_id"], h["position"]))
            out.append(item)
            continue
        # A carried-over or self-pending row would otherwise "exclude" itself; history handled those above.
        v = filters.check(x, exclusions, company_key=_company_key, role_match=_same_role,
                          company_match=tracker.same_company)
        if not v["ok"]:
            item.update(status="excluded", reasons=v["reasons"], text=v["reasons"][0]["text"])
        else:
            seen = state.first_seen(conn, x["ids"])
            newest = max(seen.values()) if seen else 0
            if newest <= cutoff:
                day = datetime.datetime.fromtimestamp(cutoff, datetime.timezone.utc).date().isoformat()
                item.update(status="backlog",
                            text="Posted on or before %s, the date you had screened up to." % day)
            else:
                item.update(status="waiting",
                            text="Eligible and not shown yet: number %d of %d waiting."
                                 % (place.get(x["key"], 0), len(waiting)))
        out.append(item)
    return out
