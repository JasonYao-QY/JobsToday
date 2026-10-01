"""SQLite state for jobs/: what the feed has shown us, what we have shown
the owner, what is excluded, and (step 5) cached verification verdicts.

Internal to the slice. batch.py is the only caller; nothing outside jobs/
opens this database or knows its layout.

Why SQLite (proposal §5): the verdict cache is the one dataset that costs
money to rebuild, so writes are transactional; WAL lets a background fetch
write while the window reads. The raw feed stays a plain cached file
(listings.py), never a blob in here.

The seen state (proposal §4) is the point of this module. Every feed
variant gets a `first_seen` the first time a sync meets it. A batch offers
listings with a variant first seen after the backlog cutoff (the
watermark, set once) and never anything already shown. Being shown is
what retires a listing; the cutoff never moves, so a listing that is
eligible but misses one batch's cut is offered again in the next. The very first sync has no
history, so it takes first_seen from the upstream post date instead of
"now": otherwise every months-old posting would look new on day one, which
is exactly how the first top-20 attempt filled itself with June and August
postings he had already screened.
"""

import contextlib
import json
import os
import sqlite3
import threading
import time

__all__ = [
    "open_db",
    "sync_seen",
    "new_since",
    "watermark",
    "set_watermark",
    "create_batch",
    "last_batch",
    "batch_rows",
    "open_batch",
    "save_summary",
    "load_summary",
    "set_row_detail",
    "shown_history",
    "first_seen",
    "mark",
    "shown_keys",
    "add_exclusion",
    "remove_exclusion",
    "list_exclusions",
    "import_exclusions",
    "seed_info",
    "exclusion_union",
    "get_verdict",
    "put_verdict",
]

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants (
    id          TEXT PRIMARY KEY,         -- "simplify:<uuid>"
    listing_key TEXT NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    posted      INTEGER NOT NULL DEFAULT 0,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS variants_listing ON variants(listing_key);
CREATE INDEX IF NOT EXISTS variants_first_seen ON variants(first_seen);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL,
    requested   INTEGER NOT NULL,
    since       INTEGER NOT NULL,         -- the watermark this batch used
    closed_at   INTEGER
);
CREATE TABLE IF NOT EXISTS shown (
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    position    INTEGER NOT NULL,         -- 1-based, what he says aloud
    listing_key TEXT NOT NULL,
    variant_id  TEXT NOT NULL,
    company     TEXT NOT NULL,            -- full upstream string, for report_applied
    title       TEXT NOT NULL,
    city        TEXT,
    url         TEXT,
    resume      TEXT,
    detail      TEXT,                     -- JSON: rank explanation, flags
    status      TEXT NOT NULL DEFAULT 'shown'
                CHECK (status IN ('shown', 'applied', 'skipped', 'carried')),
    app_id      TEXT,
    PRIMARY KEY (batch_id, position)
);
CREATE INDEX IF NOT EXISTS shown_listing ON shown(listing_key);

CREATE TABLE IF NOT EXISTS exclusions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT NOT NULL,
    title       TEXT,                     -- NULL: whole company
    kind        TEXT NOT NULL
                CHECK (kind IN ('applied', 'banned', 'capped', 'pending', 'defense',
                                'ineligible')),
    note        TEXT,
    origin      TEXT NOT NULL DEFAULT 'manual',
    created_at  INTEGER NOT NULL
);
-- NULL titles are distinct under a plain UNIQUE, so key on COALESCE.
CREATE UNIQUE INDEX IF NOT EXISTS exclusions_unique
    ON exclusions(company COLLATE NOCASE, COALESCE(title, ''), kind);

CREATE TABLE IF NOT EXISTS verdicts (
    page_hash   TEXT NOT NULL,
    verifier    TEXT NOT NULL,            -- implementation + model + prompt version
    url         TEXT,
    verdict     TEXT NOT NULL,            -- JSON
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (page_hash, verifier)
);
"""


_open_lock = threading.Lock()


def _schema_statements():
    """_SCHEMA as separate statements, so they can run inside one transaction (executescript commits
    first and runs outside any)."""
    out, buf = [], ""
    for line in _SCHEMA.splitlines(True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    return out


def _initialise(conn):
    """Put the database in WAL mode and create the schema, once.

    Two connections may arrive at a brand-new file together: the first Prepare creates it in the jobs
    thread while the page's poll opens it from the UI thread. Changing the journal mode needs the file
    to itself and does not wait politely, and "is there a schema row? no, insert one" is a race. So an
    already-initialised database is only read here, creation happens in one IMMEDIATE transaction with
    INSERT OR IGNORE, and a "locked" answer is retried for a few seconds instead of surfacing.
    """
    deadline = time.time() + 10
    while True:
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            ready = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
            row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone() if ready else None
            if row is None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _schema_statements():
                        conn.execute(stmt)
                    conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES ('schema', ?)",
                                 (str(SCHEMA_VERSION),))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            return int(row["v"])
        except sqlite3.OperationalError as e:
            if not ("locked" in str(e) or "busy" in str(e)) or time.time() > deadline:
                raise
            time.sleep(0.05)


def open_db(path):
    """Open (creating if needed) the jobs database at `path`. Safe to call from several threads at once,
    including on a database that does not exist yet. Opening an existing database writes nothing."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with _open_lock:
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            version = _initialise(conn)
        except BaseException:
            conn.close()
            raise
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError("jobs.db schema %s is newer than this code (%s)" % (version, SCHEMA_VERSION))
    return conn


@contextlib.contextmanager
def _tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _now(now):
    return int(now if now is not None else time.time())


def _meta_get(conn, k, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def _meta_set(conn, k, v):
    conn.execute("INSERT INTO meta(k, v) VALUES (?, ?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


# --------------------------------------------------------------------------
# the seen watermark

def sync_seen(conn, listings, now=None):
    """Record every variant of every listing. Returns the number new.

    First sync ever: first_seen is the upstream post date (see module
    docstring). After that: first_seen is now for any id we have not met.
    Existing rows only get last_seen bumped; first_seen never moves.
    """
    now = _now(now)
    with _tx(conn):
        bootstrap = _meta_get(conn, "synced_once") is None
        before = conn.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
        for x in listings:
            for v in x["variants"]:
                first = (v.get("posted") or now) if bootstrap else now
                conn.execute(
                    "INSERT INTO variants(id, listing_key, company, title, posted,"
                    " first_seen, last_seen) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
                    " listing_key=excluded.listing_key",
                    (v["id"], x["key"], v["company"], v["title"],
                     v.get("posted") or 0, first, now))
        new = conn.execute("SELECT COUNT(*) FROM variants").fetchone()[0] - before
        if bootstrap:
            _meta_set(conn, "synced_once", now)
        _meta_set(conn, "last_sync", now)
    return new


def watermark(conn):
    """The one-time backlog cutoff: variants first seen at or before it are
    treated as already screened. Set once by set_watermark (batch.py does it
    on first run); never advanced by batches. 0 if never set, which means
    *no* backlog protection: the whole feed is offered."""
    return int(_meta_get(conn, "watermark", 0))


def set_watermark(conn, ts):
    """Set by hand: the first batch uses this to mean "I screened
    everything up to this date already"."""
    with _tx(conn):
        _meta_set(conn, "watermark", int(ts))


def new_since(conn, listings, since=None):
    """The listings with at least one variant first seen after `since`
    (default: the watermark) and never shown in any batch. Order kept."""
    since = watermark(conn) if since is None else since
    shown = shown_keys(conn)
    fresh = {r["listing_key"] for r in conn.execute(
        "SELECT DISTINCT listing_key FROM variants WHERE first_seen > ?", (since,))}
    return [x for x in listings if x["key"] in fresh and x["key"] not in shown]


def shown_keys(conn):
    return {r["listing_key"] for r in conn.execute("SELECT DISTINCT listing_key FROM shown")}


# --------------------------------------------------------------------------
# batches

def create_batch(conn, rows, requested, now=None):
    """Record a batch as shown. rows: dicts with listing_key, variant_id,
    company, title, city, url, resume, detail (any JSON-able). Position is
    the list order, from 1. Writes the current watermark onto the batch row
    as history (`since`) but leaves the global watermark alone. Returns
    batch id."""
    now = _now(now)
    with _tx(conn):
        since = int(_meta_get(conn, "watermark", 0))
        cur = conn.execute("INSERT INTO batches(created_at, requested, since) VALUES (?,?,?)",
                           (now, int(requested), since))
        bid = cur.lastrowid
        for i, r in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO shown(batch_id, position, listing_key, variant_id, company,"
                " title, city, url, resume, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bid, i, r["listing_key"], r["variant_id"], r["company"], r["title"],
                 r.get("city"), r.get("url"), r.get("resume"),
                 json.dumps(r.get("detail"), sort_keys=True) if r.get("detail") is not None else None))
        # The global watermark does NOT move here. shown_keys() already keeps
        # shown listings out; moving the cutoff as well would silently drop
        # every eligible listing that missed this batch's cut, forever.
    return bid


def last_batch(conn):
    row = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def batch_rows(conn, batch_id):
    out = []
    for r in conn.execute("SELECT * FROM shown WHERE batch_id=? ORDER BY position", (batch_id,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


def mark(conn, batch_id, positions, status, app_ids=None):
    """Set status on positions of one batch. app_ids: {position: app_id}.

    shown    offered, not yet reported: the batch stays open
    applied  he applied (app_id links the tracker record)
    skipped  he said no; retired for good
    carried  unfinished here, moved to the top of a later batch (batch.py)

    A batch closes when nothing in it is left at 'shown'."""
    if status not in ("shown", "applied", "skipped", "carried"):
        raise ValueError(status)
    app_ids = app_ids or {}
    with _tx(conn):
        for p in positions:
            conn.execute("UPDATE shown SET status=?, app_id=COALESCE(?, app_id)"
                         " WHERE batch_id=? AND position=?",
                         (status, app_ids.get(p), batch_id, int(p)))
        left = conn.execute("SELECT COUNT(*) FROM shown WHERE batch_id=? AND status='shown'",
                            (batch_id,)).fetchone()[0]
        conn.execute("UPDATE batches SET closed_at=? WHERE id=?",
                     (int(time.time()) if left == 0 else None, batch_id))


def save_summary(conn, summary):
    """Remember what the last prepare found, so the tab can say it without preparing again."""
    with _tx(conn):
        _meta_set(conn, "last_prepare", json.dumps(summary, sort_keys=True))


def load_summary(conn):
    raw = _meta_get(conn, "last_prepare")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def open_batch(conn):
    """The newest batch that still has rows at 'shown', or None."""
    row = conn.execute("SELECT b.* FROM batches b WHERE EXISTS (SELECT 1 FROM shown s"
                       " WHERE s.batch_id=b.id AND s.status='shown') ORDER BY b.id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def set_row_detail(conn, batch_id, position, key, value):
    """Merge one key into a row's detail JSON (the lazy verdict lands here)."""
    with _tx(conn):
        row = conn.execute("SELECT detail FROM shown WHERE batch_id=? AND position=?",
                           (batch_id, int(position))).fetchone()
        if row is None:
            raise KeyError((batch_id, position))
        detail = json.loads(row["detail"]) if row["detail"] else {}
        detail[key] = value
        conn.execute("UPDATE shown SET detail=? WHERE batch_id=? AND position=?",
                     (json.dumps(detail, sort_keys=True), batch_id, int(position)))


def shown_history(conn, listing_keys):
    """Every time any of these listings was shown: batch, position, status."""
    keys = list(listing_keys)
    if not keys:
        return []
    q = ("SELECT batch_id, position, listing_key, company, title, status FROM shown"
         " WHERE listing_key IN (%s) ORDER BY batch_id, position" % ",".join("?" * len(keys)))
    return [dict(r) for r in conn.execute(q, keys)]


def first_seen(conn, variant_ids):
    """{variant_id: first_seen} for the ids we have met."""
    ids = list(variant_ids)
    if not ids:
        return {}
    q = "SELECT id, first_seen FROM variants WHERE id IN (%s)" % ",".join("?" * len(ids))
    return {r["id"]: r["first_seen"] for r in conn.execute(q, ids)}


# --------------------------------------------------------------------------
# exclusions

def add_exclusion(conn, company, kind, title=None, note=None, origin="manual", now=None):
    with _tx(conn):
        conn.execute("INSERT OR IGNORE INTO exclusions(company, title, kind, note, origin,"
                     " created_at) VALUES (?,?,?,?,?,?)",
                     (company.strip(), (title or None) and title.strip(), kind, note,
                      origin, _now(now)))


def remove_exclusion(conn, exclusion_id):
    with _tx(conn):
        conn.execute("DELETE FROM exclusions WHERE id=?", (int(exclusion_id),))


def list_exclusions(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM exclusions ORDER BY kind, company, title")]


def import_exclusions(conn, path, now=None):
    """Make the stored seed entries match a seed file: {"taken": "YYYY-MM-DD",
    "exclusions": [{company, title?, kind, note?}]}. Returns the number of
    entries read.

    The file is the list. Every entry that came from an earlier import is
    replaced by what the file holds now, so an entry added to the file
    appears, an edited note is picked up, and an entry removed from the
    file is removed here. Entries added by hand (add_exclusion) are never
    touched. Importing the same file twice changes nothing.

    A missing or malformed file imports nothing and removes nothing: an
    unreadable seed must not silently lift every exclusion. A readable
    file with an empty list does clear the seed entries; that is a
    decision someone wrote down.

    The seed is a point-in-time copy of a list kept outside the app, so it
    drifts. Its `taken` date is recorded (seed_taken) so the UI can say how
    old the copy is; seed_imported records when it was last loaded."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data["exclusions"] if isinstance(data, dict) else data
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if not isinstance(items, list):
        return 0
    n = 0
    now = _now(now)
    with _tx(conn):
        conn.execute("DELETE FROM exclusions WHERE origin='seed'")
        for e in items:
            if not isinstance(e, dict) or not isinstance(e.get("company"), str) \
                    or not e["company"].strip() or e.get("kind") not in (
                    "applied", "banned", "capped", "pending", "defense", "ineligible"):
                continue
            title = e.get("title") if isinstance(e.get("title"), str) and e["title"].strip() else None
            conn.execute("INSERT OR IGNORE INTO exclusions(company, title, kind, note, origin,"
                         " created_at) VALUES (?,?,?,?,?,?)",
                         (e["company"].strip(), title and title.strip(),
                          e["kind"], e.get("note") if isinstance(e.get("note"), str) else None,
                          "seed", now))
            n += 1
        taken = data.get("taken") if isinstance(data, dict) else None
        if isinstance(taken, str):
            _meta_set(conn, "seed_taken", taken)
        else:
            conn.execute("DELETE FROM meta WHERE k='seed_taken'")
        _meta_set(conn, "seed_imported", now)
    return n


def seed_info(conn):
    """{taken, imported} for the last seed import; None values if never."""
    imported = _meta_get(conn, "seed_imported")
    return {"taken": _meta_get(conn, "seed_taken"),
            "imported": int(imported) if imported else None}


def exclusion_union(conn, applications=()):
    """Everything filters.check must not suggest (proposal §4):

    - applications.json, passed in by batch.py from tracker (company, role)
    - the stored exclusions: banned, capped, defense, and the applied/pending
      entries seeded from the memory file that never sent mail
    - every position of an open batch not yet reported, as pending

    Shown-and-skipped listings are kept out by the seen watermark rather
    than here, so this list stays readable in the UI.
    """
    out = []
    for a in applications:
        if a.get("company"):
            out.append({"company": a["company"], "title": a.get("role") or a.get("title"),
                        "kind": "applied", "note": a.get("status")})
    for e in list_exclusions(conn):
        out.append({"company": e["company"], "title": e["title"], "kind": e["kind"],
                    "note": e["note"], "id": e["id"]})
    for r in conn.execute(
            "SELECT s.company, s.title, s.batch_id FROM shown s"
            " WHERE s.status='shown'"):
        out.append({"company": r["company"], "title": r["title"], "kind": "pending",
                    "note": "batch %d" % r["batch_id"]})
    return out


# --------------------------------------------------------------------------
# verification cache (filled in step 5)

def get_verdict(conn, page_hash, verifier):
    row = conn.execute("SELECT verdict FROM verdicts WHERE page_hash=? AND verifier=?",
                       (page_hash, verifier)).fetchone()
    return json.loads(row["verdict"]) if row else None


def put_verdict(conn, page_hash, verifier, verdict, url=None, now=None):
    with _tx(conn):
        conn.execute("INSERT INTO verdicts(page_hash, verifier, url, verdict, created_at)"
                     " VALUES (?,?,?,?,?) ON CONFLICT(page_hash, verifier) DO UPDATE"
                     " SET verdict=excluded.verdict, url=excluded.url,"
                     " created_at=excluded.created_at",
                     (page_hash, verifier, url, json.dumps(verdict, sort_keys=True),
                      _now(now)))
