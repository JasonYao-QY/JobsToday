#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Today — the window version of the `today` scheduler.

Opens a native macOS window (pywebview / WKWebView). The page is loaded from memory, so there is no
web server and no localhost port. Every read and write goes through today.py, the same code the
`today` command uses, so the window and the terminal always see the same plans, daily log, deadlines
and inbox.

  ~/.config/today/venv/bin/python ~/.config/today/today_gui.py      (or open ~/Applications/Today.app)

TODAY_HOME=/some/dir points it at another data folder, same as the CLI. TODAY_DEBUG=1 enables the
web inspector (right-click → Inspect Element).
"""

import datetime as dt
import fcntl
import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import today as T  # noqa: E402  (the CLI's module: data files, parsing, mail scan)
import tracker as TR  # noqa: E402  (application tracker: applications.json)

LOG_DIR = os.path.join(T.APP_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "gui.log")
HTML_FILE = os.path.join(HERE, "gui.html")
ICON_FILE = os.path.join(HERE, "icon.png")
LOCK_FILE = os.path.join(T.APP_DIR, ".gui.lock")
RAISE_FILE = os.path.join(T.APP_DIR, ".gui.raise")

DUR_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m(?:in)?)?$", re.I)


# ----------------------------------------------------------------------------
# log: ~/.config/today/logs/gui.log (and the terminal, when started from one). Never logs passwords.
# ----------------------------------------------------------------------------

_log_lock = threading.Lock()


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _log_lock:
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def rotate_log():
    try:
        if os.path.getsize(LOG_FILE) > 1000000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def safe(fn):
    """Every API call returns JSON; a Python error comes back as {"error": "..."} instead of a dead promise."""
    @functools.wraps(fn)
    def wrap(*a, **k):
        try:
            out = fn(*a, **k)
            return {"ok": True} if out is None else out
        except UserError as e:
            if fn.__name__ not in ("version",):
                log("%s: %s" % (fn.__name__, e))
            return {"error": str(e)}
        except Exception as e:  # keep the window alive; log for debugging (never the arguments: may hold a password)
            log("ERROR in %s: %s\n%s" % (fn.__name__, e, traceback.format_exc()))
            return {"error": "%s: %s" % (e.__class__.__name__, e)}
    return wrap


class UserError(Exception):
    pass


def parse_duration(tok):
    """'75m', '90min', '1h', '1.5h', '1h30m' -> minutes, else None."""
    m = DUR_RE.match(tok or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(round(float(m.group(1) or 0) * 60)) + int(m.group(2) or 0) or None


def to_mins(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else None
    s = str(v).strip().replace(" ", "")
    if s.isdigit():
        return int(s) or None
    m = parse_duration(s)
    if m is None:
        raise UserError("duration not understood: %r (try 45, 45m, 1h, 1h30m)" % v)
    return m


def check_date(date_str):
    try:
        T.to_date(date_str)
    except (TypeError, ValueError):
        raise UserError("bad date %r" % date_str)
    return date_str


def rel_label(date_str):
    d = T.to_date(date_str)
    off = (d - dt.date.today()).days
    return {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(off, "")


def dark_mode():
    try:
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=3)
        return "dark" in r.stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


# ----------------------------------------------------------------------------
# the API the page calls (window.pywebview.api.<name>)
# ----------------------------------------------------------------------------

class Api(object):
    def __init__(self):
        self._window = None          # underscore: pywebview must not expose or serialize it
        self._scan_lock = threading.Lock()
        self._scanning = False
        self._scan_log = []

    def _push(self, fn, payload):
        if fn == "onScan":
            log("scan: %s" % (payload.get("progress") or json.dumps(payload)))
        w = self._window
        if w is not None:
            try:
                w.evaluate_js("window.%s && window.%s(%s)" % (fn, fn, json.dumps(payload)))
            except Exception:
                pass

    # ---- reading ----------------------------------------------------------

    @safe
    def version(self):
        """Cheap change signature: the page polls this and reloads when the CLI (or anything) edits a file."""
        parts = [T.today_str()]
        for p in (T.DAILY_FILE, T.DEADLINES_FILE, T.INBOX_FILE, T.CONFIG_FILE, TR.path(T.APP_DIR)):
            try:
                parts.append("%.3f" % os.stat(p).st_mtime)
            except OSError:
                parts.append("-")
        try:
            for n in sorted(os.listdir(T.PLANS_DIR)):
                if n.endswith(".json"):
                    parts.append("%s:%.3f" % (n, os.stat(os.path.join(T.PLANS_DIR, n)).st_mtime))
        except OSError:
            pass
        return {"v": "|".join(parts), "scanning": self._scanning}

    @safe
    def state(self, date=None, show_hidden=False):
        today = T.today_str()
        date = check_date(date or today)

        plan = T.load_plan(date)
        items = []
        for i in T.sorted_items(plan["items"]):
            mins = i.get("mins")
            items.append({
                "id": i["id"], "title": i.get("title", ""), "time": i.get("time") or "",
                "time_fmt": T.fmt_time(i.get("time") or ""),
                "end_fmt": T.fmt_time(T.add_minutes(i["time"], mins)) if i.get("time") and mins else "",
                "mins": mins, "mins_fmt": T.fmt_minutes(mins) if mins else "",
                "note": i.get("note") or "", "urgent": bool(i.get("urgent")), "done": bool(i.get("done")),
            })

        carry = {"n": 0}
        if date >= today:
            cand, src = T.carry_candidates(date)
            have = {i["title"].strip().lower() for i in plan["items"]}
            n = len([c for c in cand if c["title"].strip().lower() not in have])
            if n and src:
                carry = {"n": n, "from": src, "from_fmt": T.short_date(src)}

        d = T.load_daily()
        daily = []
        for it in d["items"]:
            dn, cnt = T.daily_state(d, it, date)
            daily.append({"id": it["id"], "title": it["title"], "target": it.get("target"), "count": cnt, "done": dn})

        inbox = T.load_inbox()
        deadlines = []
        for x in T.all_deadlines(T.load_deadlines(), inbox):
            x = dict(x)
            x["when_fmt"] = T.deadline_when(x)
            deadlines.append(x)

        rows = []
        for i in T.inbox_visible(inbox, days=30, include_hidden=bool(show_hidden)):
            kind, st = i.get("kind"), i.get("status")
            due_open = bool(i.get("due")) and st == "open"
            txt, sev = T.until(i["due"]) if due_open else ("", "none")
            rows.append({
                "id": i["id"], "kind": kind, "status": st, "company": i.get("company") or "",
                "title": i.get("title") or "", "detail": i.get("detail") or "", "platform": i.get("platform") or "",
                "label": "OA ✓" if kind == "oa" and st == "done" else T.KIND_LABEL.get(kind, "?"),
                "date_fmt": T.short_date(i.get("date", "")), "account": i.get("account", ""),
                "due_fmt": T.fmt_when(i["due"]) if due_open else "", "until": txt, "sev": sev,
                "link": i.get("link") or "",
            })

        cfg = T.load_config()
        tr = self._tracker()
        return {
            "apps_totals": TR.totals(tr), "apps_history": bool(tr["history"]),
            "date": date, "today": today, "is_today": date == today, "is_past": date < today,
            "human": T.human_date(date), "rel": rel_label(date),
            "items": items, "carry": carry, "daily": daily, "deadlines": deadlines,
            "inbox": rows, "counts": T.inbox_counts(inbox), "summary": T.inbox_summary(inbox),
            "inbox_updated": inbox.get("updated", ""), "inbox_errors": inbox.get("errors") or {},
            "accounts": cfg.get("accounts") or [], "scanning": self._scanning,
            "version": self.version()["v"],
        }

    # ---- plan -------------------------------------------------------------

    def _plan_item(self, plan, item_id):
        for i in plan["items"]:
            if i["id"] == item_id:
                return i
        raise UserError("that item no longer exists (edited elsewhere?)")

    @safe
    def plan_add(self, date, text, note="", urgent=False):
        """Quick add, like the CLI: '6:10pm Norvia OA 75m' -> time 18:10, title 'Norvia OA', 75 min."""
        tokens = (text or "").split()
        hhmm, rest = T.split_time_prefix(tokens)
        mins = None
        if len(rest) > 1:
            m = parse_duration(rest[-1])
            if m:
                mins, rest = m, rest[:-1]
        title = " ".join(rest).strip()
        if not title:
            raise UserError("type a title, optionally starting with a time: 6:10pm Norvia OA 75m")
        plan = T.load_plan(check_date(date))
        it = T.add_item(plan, title, hhmm, note, mins, urgent)
        T.save_plan(plan)
        return {"id": it["id"]}

    @safe
    def plan_update(self, date, item_id, fields):
        plan = T.load_plan(check_date(date))
        it = self._plan_item(plan, item_id)
        f = fields or {}
        if "title" in f:
            t = (f["title"] or "").strip()
            if not t:
                raise UserError("title can't be empty")
            it["title"] = t
        if "time" in f:
            s = (f["time"] or "").strip()
            if s:
                hhmm = T.parse_time(s)
                if not hhmm:
                    raise UserError("time not understood: %r (try 6:10pm, 18:10, 6pm)" % s)
                it["time"] = hhmm
            else:
                it["time"] = ""
        if "mins" in f:
            it["mins"] = to_mins(f["mins"])
        if "note" in f:
            it["note"] = (f["note"] or "").strip()
        if "urgent" in f:
            it["urgent"] = bool(f["urgent"])
        if "done" in f:
            it["done"] = bool(f["done"])
        T.save_plan(plan)

    @safe
    def plan_shift(self, date, item_id, delta):
        plan = T.load_plan(check_date(date))
        it = self._plan_item(plan, item_id)
        if not it.get("time"):
            raise UserError("give it a time first")
        it["time"] = T.add_minutes(it["time"], int(delta))
        T.save_plan(plan)

    @safe
    def plan_remove(self, date, item_id):
        plan = T.load_plan(check_date(date))
        plan["items"] = [i for i in plan["items"] if i["id"] != item_id]
        T.save_plan(plan)

    @safe
    def plan_move(self, date, item_id, target):
        """Move an item to another day (e.g. 'tomorrow')."""
        check_date(target)
        if target == date:
            return
        src = T.load_plan(check_date(date))
        it = self._plan_item(src, item_id)
        dst = T.load_plan(target)
        n = T.add_item(dst, it["title"], it.get("time", ""), it.get("note", ""), it.get("mins"), it.get("urgent"))
        T.save_plan(dst)
        src["items"] = [i for i in src["items"] if i["id"] != item_id]
        T.save_plan(src)
        return {"id": n["id"]}

    @safe
    def plan_carry(self, date):
        plan = T.load_plan(check_date(date))
        n, src = T.carry_over(plan)
        T.save_plan(plan)
        return {"n": n, "from": src or ""}

    # ---- daily ------------------------------------------------------------

    def _daily_item(self, d, item_id):
        for it in d["items"]:
            if it["id"] == item_id:
                return it
        raise UserError("that daily task no longer exists")

    @safe
    def daily_add(self, title, target=None):
        title = (title or "").strip()
        if not title:
            raise UserError("type a daily task")
        d = T.load_daily()
        tgt = int(target) if target not in (None, "") else T.guess_target(title)
        d["items"].append({"id": T.new_id(), "title": title, "target": tgt})
        T.save_daily(d)

    @safe
    def daily_toggle(self, item_id, date):
        d = T.load_daily()
        it = self._daily_item(d, item_id)
        dn, _ = T.daily_state(d, it, check_date(date))
        T.daily_set(d, it, date, done=not dn)
        T.save_daily(d)

    @safe
    def daily_inc(self, item_id, date, k=1):
        d = T.load_daily()
        it = self._daily_item(d, item_id)
        _, cnt = T.daily_state(d, it, check_date(date))
        T.daily_set(d, it, date, count=cnt + int(k))
        T.save_daily(d)

    @safe
    def daily_edit(self, item_id, title, target=None):
        d = T.load_daily()
        it = self._daily_item(d, item_id)
        t = (title or "").strip()
        if t:
            it["title"] = t
        # target: a number >= 2 makes it a counter (0/20); empty means a plain checkbox
        it["target"] = int(target) if target not in (None, "", 0, "0") and int(target) >= 2 else None
        T.save_daily(d)

    @safe
    def daily_remove(self, item_id):
        d = T.load_daily()
        d["items"] = [i for i in d["items"] if i["id"] != item_id]
        T.save_daily(d)

    # ---- deadlines --------------------------------------------------------

    @safe
    def deadline_add(self, name, when="", note=""):
        name = (name or "").strip()
        if not name:
            raise UserError("give the deadline a name")
        at = ""
        if (when or "").strip():
            at = T.parse_when(when)
            if not at:
                raise UserError("time not understood: %r (try Oct 16, Oct 1 11:27am, tomorrow 9pm)" % when)
        d = T.load_deadlines()
        d["items"].append({"id": T.new_id(), "name": name, "at": at, "note": (note or "").strip(), "done": False})
        T.save_deadlines(d)

    @safe
    def deadline_act(self, item_id, source, action):
        if action not in ("done", "undo", "remove"):
            raise UserError("unknown action")
        if source == "inbox":
            inbox = T.load_inbox()
            for i in inbox["items"]:
                if i["id"] == item_id:
                    i["status"] = {"done": "done", "undo": "open", "remove": "hidden"}[action]
            T.save_inbox(inbox)
            return
        d = T.load_deadlines()
        if action == "remove":
            d["items"] = [i for i in d["items"] if i["id"] != item_id]
        else:
            for i in d["items"]:
                if i["id"] == item_id:
                    i["done"] = action == "done"
        T.save_deadlines(d)

    # ---- inbox ------------------------------------------------------------

    @safe
    def inbox_act(self, item_id, action):
        inbox = T.load_inbox()
        for i in inbox["items"]:
            if i["id"] != item_id:
                continue
            if action == "toggle_done":
                if i.get("kind") != "oa":
                    raise UserError("only assessments can be marked done")
                i["status"] = "open" if i.get("status") == "done" else "done"
            elif action == "hide":
                i["status"] = "hidden"
            elif action == "unhide":
                i["status"] = "open"
            else:
                raise UserError("unknown action")
        T.save_inbox(inbox)

    @safe
    def log_js(self, msg):
        log("page error: %s" % str(msg)[:2000])

    @safe
    def open_url(self, url):
        if not re.match(r"^https://[\w.-]+(/|$)", url or ""):
            raise UserError("not an https link")
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- application tracker ----------------------------------------------------

    def _tracker(self):
        tr = TR.load(T.APP_DIR)
        if not tr["apps"] and not tr["seen"]:
            # first look: start from what the inbox already knows; Check mail fills in the full history
            if TR.ingest_items(tr, T.load_inbox()["items"]):
                TR.save(T.APP_DIR, tr)
        return tr

    @safe
    def apps(self):
        tr = self._tracker()
        rows = []
        for a in tr["apps"]:
            r = TR.derive(a)
            r["ckey"] = TR.company_key(r["company"])
            for e in r["events"]:
                e["date_fmt"] = T.short_date(e["date"])
            for k in ("applied", "last", "stage_date", "ended"):
                r[k + "_fmt"] = T.short_date(r[k]) if r[k] else ""
            rows.append(r)
        cfg = T.load_config()
        return {"apps": rows, "totals": TR.totals(tr), "weekly": TR.weekly(tr, 12),
                "history": tr["history"], "history_since": cfg.get("history_since") or TR.DEFAULT_HISTORY_SINCE,
                "history_since_fmt": T.short_date(cfg.get("history_since") or TR.DEFAULT_HISTORY_SINCE),
                "accounts": cfg.get("accounts") or [], "updated": tr.get("updated", ""),
                "stages": [[k, TR.STAGE_LABEL[k]] for k in TR.STAGES],
                "statuses": [[k, TR.STATUS_LABEL[k]] for k in TR.STATUSES]}

    def _tr_edit(self, fn):
        tr = TR.load(T.APP_DIR)
        try:
            out = fn(tr)
        except KeyError:
            raise UserError("that application no longer exists (changed elsewhere?)")
        except ValueError as e:
            raise UserError(str(e))
        TR.save(T.APP_DIR, tr)
        return out

    @safe
    def app_update(self, app_id, fields):
        self._tr_edit(lambda tr: TR.update(tr, app_id, fields or {}))

    @safe
    def app_delete(self, app_id):
        self._tr_edit(lambda tr: TR.delete(tr, app_id))

    @safe
    def app_move_event(self, key, target):
        dst = self._tr_edit(lambda tr: TR.move_event(tr, key, target))
        return {"id": dst["id"]}

    @safe
    def app_merge(self, src_id, dst_id):
        dst = self._tr_edit(lambda tr: TR.merge(tr, src_id, dst_id))
        return {"id": dst["id"]}

    @safe
    def set_history_since(self, date):
        try:
            d = dt.date.fromisoformat((date or "").strip())
        except ValueError:
            raise UserError("use a date like 2026-06-01")
        cfg = T.load_config()
        cfg["history_since"] = d.isoformat()
        T.save_config(cfg)

    # ---- mail accounts + scan -----------------------------------------------

    @safe
    def accounts(self):
        cfg = T.load_config()
        return {"accounts": [{"email": a, "has_password": T.has_password(a)} for a in cfg["accounts"]],
                "scan_days": cfg.get("scan_days", 30)}

    @safe
    def mail_add(self, email, app_password):
        acct = (email or "").strip().lower()
        log("mail_add: testing IMAP login for %s" % acct)
        try:
            where = T.add_account(email, app_password)
        except ValueError as e:
            raise UserError(str(e))
        except T.LoginFailed as e:
            log("mail_add: login FAILED for %s: %s" % (acct, e))
            raise UserError("login failed: %s. Is IMAP on, and is this an app password (not your normal one)? "
                            "School accounts may block app passwords; forwarding that mail to Gmail works too."
                            % str(e)[:140])
        log("mail_add: %s ok, password stored in %s" % (acct, where))
        return {"saved": where}

    @safe
    def mail_remove(self, email):
        T.remove_account(email)

    @safe
    def scan(self, history=False):
        cfg = T.load_config()
        if not cfg["accounts"]:
            raise UserError("add a mail account first (Mail accounts in the sidebar)")
        with self._scan_lock:
            if self._scanning:
                return {"started": False, "running": True}
            self._scanning = True
        log("scan: START (history=%s) accounts=%s history_since=%s" % (bool(history), cfg["accounts"], cfg.get("history_since")))
        threading.Thread(target=self._scan_worker, args=(cfg, bool(history)), daemon=True).start()
        return {"started": True}

    def _scan_worker(self, cfg, history=False):
        t0 = time.time()
        try:
            res = T.scan_all(cfg, progress=lambda t: self._push("onScan", {"progress": t}), history=history)
            out = {"done": True, "found": res["found"], "errors": res["errors"], "tracker": res.get("tracker")}
            log("scan: FINISHED in %.0fs, %d relevant messages, per account %s, errors %s, tracker %s"
                % (time.time() - t0, res["found"], res["counts"], res["errors"] or "none", res.get("tracker")))
        except Exception as e:
            log("scan: CRASHED after %.0fs: %s\n%s" % (time.time() - t0, e, traceback.format_exc()))
            out = {"done": True, "found": 0, "errors": {"scan": str(e)}}
        finally:
            self._scanning = False
        self._push("onScan", out)


# ----------------------------------------------------------------------------
# macOS niceties: one instance, app name and icon, bring to front
# ----------------------------------------------------------------------------

def single_instance():
    """Returns the held lock file, or None if another window is already open (it gets asked to come forward)."""
    os.makedirs(T.APP_DIR, exist_ok=True)
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        with open(RAISE_FILE, "w") as r:
            r.write("%f\n" % time.time())
        f.close()
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f


def mac_prelude():
    """Show 'Today' (not 'Python') in the menu bar, and our icon in the Dock. Must run before the app starts."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
        b = NSBundle.mainBundle()
        info = b.localizedInfoDictionary() or b.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Today"
    except Exception:
        pass
    try:
        from AppKit import NSApplication, NSImage
        if os.path.exists(ICON_FILE):
            img = NSImage.alloc().initWithContentsOfFile_(ICON_FILE)
            if img is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def bring_forward(win):
    try:
        win.restore()
        win.show()
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda: NSApplication.sharedApplication().activateIgnoringOtherApps_(True))
        except Exception:
            pass


def watch_raise(win):
    """A second launch (Dock, Spotlight, login item) touches RAISE_FILE; bring this window forward."""
    def mtime():
        try:
            return os.stat(RAISE_FILE).st_mtime
        except OSError:
            return 0
    last = mtime()
    while True:
        time.sleep(0.5)
        m = mtime()
        if m != last:
            last = m
            bring_forward(win)


def main():
    lock = single_instance()
    if lock is None:
        return 0
    rotate_log()
    try:
        import webview
    except ImportError:
        sys.stderr.write("pywebview is not installed. Run: sh ~/.config/today/install-gui.sh\n")
        return 1
    mac_prelude()
    log("start: pid %d, python %s, pywebview %s, data %s" % (os.getpid(), sys.version.split()[0],
                                                              getattr(webview, "__version__", "?"), T.APP_DIR))
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    api = Api()
    win = webview.create_window(
        "Today", html=html, js_api=api,
        width=1180, height=780, min_size=(760, 540),
        background_color="#1E1E1E" if dark_mode() else "#F5F5F7",
        text_select=True,
    )
    api._window = win

    def started():
        threading.Thread(target=watch_raise, args=(win,), daemon=True).start()
        bring_forward(win)

    webview.start(started, debug=bool(os.environ.get("TODAY_DEBUG")))
    log("quit")
    lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
