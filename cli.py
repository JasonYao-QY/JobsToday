# -*- coding: utf-8 -*-
"""today — a one-day scheduler: the `today` command, and the Today window (today_gui.py) over the same data.

  today                     print today's plan (same as: today show)
  today show                print today's plan
  today add [TIME] TITLE [--min N] [--note TEXT] [--urgent]
  today done N | undo N | rm N | move N TIME | edit N TITLE | note N TEXT
  today carry               bring unfinished items over from the previous plan
  today tomorrow ...        run a plan command against tomorrow (today tomorrow add 7pm "StoneRidge OA")
  today daily               recurring daily tasks
  today daily add TITLE [--count N] | rm N | done N | undo N | inc N [K] | set N K
  today deadline            deadlines (manual ones + open assessments found in mail)
  today deadline add NAME WHEN | rm N | done N
  today mail add EMAIL | rm EMAIL | list      accounts for the mail scan (app password -> Keychain)
  today scan [--history]    scan mail for assessments, interviews, rejections, important mail
                            (--history re-reads everything since history_since, default 2026-06-01)
  today inbox [--all]       what the last scan found
  today apps [--all]        application tracker: totals, and where each application stands
  today report COMPANY [ROLE] [--date D]   record an application you made (counts toward the daily goal)
  today help

TIME looks like 6:10pm, 18:10, 6pm.  WHEN looks like "2026-10-01 11:27", "Oct 1 11:27am", "tomorrow 9pm".
Data lives in ~/.config/today (override with TODAY_HOME)."""

import datetime as dt
import sys

import tracker
from report import report_applied
from store import APP_DIR, new_id
from timeutil import (
    fmt_minutes,
    fmt_time,
    fmt_when,
    human_date,
    parse_time,
    parse_when,
    short_date,
    split_time_prefix,
    today_str,
    until,
)
from textutil import clip
from plans import add_item, carry_over, load_plan, save_plan, sorted_items
from daily import daily_set, daily_state, guess_target, load_daily, save_daily
from deadlines import all_deadlines, deadline_when, load_deadlines, save_deadlines
from inbox import inbox_summary, inbox_visible, KIND_LABEL, load_inbox, save_inbox
from mail import add_account, has_password, KEYCHAIN_SERVICE, load_config, LoginFailed, remove_account, scan_all


VERSION = "1.1.0"


def bar_text(done, total, width=10):
    if total <= 0:
        return "▯" * width
    f = int(round(width * min(done, total) / float(total)))
    return "▮" * f + "▯" * (width - f)


def print_plan(plan, daily, deadlines, inbox):
    items = sorted_items(plan["items"])
    done = sum(1 for i in items if i.get("done"))
    print("TODAY  %s%s" % (human_date(plan["date"]), "" if plan["date"] == today_str() else "  (%s)" % plan["date"]))
    if items:
        print("       %d/%d done  %s" % (done, len(items), bar_text(done, len(items))))
    dls = all_deadlines(deadlines, inbox)
    if dls:
        print("\nDEADLINES")
        for d in dls:
            mark = "✓" if d["done"] else ("●" if d["sev"] in ("soon", "over") else "○")
            when = deadline_when(d)
            print("  %s %-28s %-22s %s" % (mark, clip(d["name"], 28), when, "" if d["done"] else d["until"]))
    print("\nSCHEDULE")
    if not items:
        print("  nothing planned — today add 6pm \"Something\"")
    for n, it in enumerate(items, 1):
        t = fmt_time(it.get("time")).rjust(8) if it.get("time") else "       –"
        flags = ""
        if it.get("mins"):
            flags += " · " + fmt_minutes(it["mins"])
        if it.get("urgent"):
            flags += " ⚠"
        print("  %2d. %s %s %s%s" % (n, t, "✓" if it.get("done") else " ", it["title"], flags))
        if it.get("note"):
            print("                   └ %s" % it["note"])
    print("\nDAILY")
    if not daily["items"]:
        print("  none — today daily add \"Finish one leetcode problem\"")
    for n, it in enumerate(daily["items"], 1):
        dn, cnt = daily_state(daily, it, plan["date"])
        if it.get("target"):
            print("  %2d. %s %2d/%-3d %s" % (n, bar_text(cnt, it["target"]), cnt, it["target"], it["title"]))
        else:
            print("  %2d. [%s] %s" % (n, "✓" if dn else " ", it["title"]))
    print("\nINBOX  %s" % (("scanned " + inbox["updated"]) if inbox["updated"] else "never scanned"))
    print("  " + inbox_summary(inbox))
    for i in inbox_visible(inbox)[:8]:
        print_inbox_item(i)


def print_inbox_item(i, n=None):
    label = KIND_LABEL.get(i.get("kind"), "?")
    if i.get("kind") == "oa" and i.get("status") == "done":
        label = "OA ✓"
    right = ("closes %s (%s)" % (fmt_when(i["due"]), until(i["due"])[0])) if i.get("due") and i.get("status") == "open" else short_date(i.get("date", ""))
    prefix = ("%2d. " % n) if n is not None else "  "
    hidden = "  (hidden)" if i.get("status") == "hidden" else ""
    print("%s%-5s %-16s %-48s %s%s" % (prefix, label, clip(i.get("company") or "", 16), clip(i.get("title", ""), 48), right, hidden))
    if i.get("detail") and i.get("kind") == "oa":
        print("%s      %s" % (" " * len(prefix), i["detail"]))


def print_inbox(inbox, show_all=False):
    print("INBOX  %s  ·  %s" % (("scanned " + inbox["updated"]) if inbox["updated"] else "never scanned", inbox_summary(inbox)))
    if inbox.get("errors"):
        for a, e in inbox["errors"].items():
            print("  ! %s: %s" % (a, e))
    items = inbox_visible(inbox, include_hidden=show_all)
    if not items:
        print("  nothing yet — today mail add you@gmail.com, then: today scan")
    for n, i in enumerate(items, 1):
        print_inbox_item(i, n)
    if show_all:
        applied = [i for i in inbox["items"] if i.get("kind") == "applied"]
        if applied:
            print("\n  application confirmations (%d):" % len(applied))
            for i in applied:
                print("    %s  %-18s %s" % (short_date(i["date"]), clip(i.get("company") or "", 18), clip(i["title"], 60)))


def _pick(items, arg, what):
    try:
        n = int(arg)
        return items[n - 1]
    except (ValueError, IndexError):
        sys.exit("no %s #%s (run `today show` for numbers)" % (what, arg))


def _flag(args, name, default=None, has_value=True):
    if name in args:
        i = args.index(name)
        if has_value:
            val = args[i + 1] if i + 1 < len(args) else default
            del args[i:i + 2]
            return val
        del args[i]
        return True
    return default


def cli_plan(args, date_str):
    plan = load_plan(date_str)
    cmd = args[0] if args else "show"
    rest = args[1:]
    items = sorted_items(plan["items"])
    if cmd in ("show", "ls", "list"):
        print_plan(plan, load_daily(), load_deadlines(), load_inbox())
        return
    if cmd == "add":
        mins = _flag(rest, "--min")
        note = _flag(rest, "--note", "")
        urgent = _flag(rest, "--urgent", False, has_value=False)
        hhmm, rest = split_time_prefix(rest)
        title = " ".join(rest).strip()
        if not title:
            sys.exit("usage: today add [TIME] TITLE [--min N] [--note TEXT] [--urgent]")
        it = add_item(plan, title, hhmm, note, int(mins) if mins else None, urgent)
        save_plan(plan)
        print("added  %s %s" % (fmt_time(it["time"]) or "(no time)", it["title"]))
        return
    if cmd in ("done", "undo", "rm", "remove", "move", "edit", "note", "urgent"):
        if not rest:
            sys.exit("usage: today %s N ..." % cmd)
        it = _pick(items, rest[0], "item")
        if cmd == "done":
            it["done"] = True
        elif cmd == "undo":
            it["done"] = False
        elif cmd in ("rm", "remove"):
            plan["items"] = [i for i in plan["items"] if i["id"] != it["id"]]
        elif cmd == "move":
            hhmm = parse_time(" ".join(rest[1:]))
            if not hhmm:
                sys.exit("time not understood: %r" % " ".join(rest[1:]))
            it["time"] = hhmm
        elif cmd == "edit":
            it["title"] = " ".join(rest[1:]).strip() or it["title"]
        elif cmd == "note":
            it["note"] = " ".join(rest[1:]).strip()
        elif cmd == "urgent":
            it["urgent"] = not it.get("urgent")
        save_plan(plan)
        print("%s  %s" % (cmd, it["title"]))
        return
    if cmd == "carry":
        n, src = carry_over(plan)
        save_plan(plan)
        print("carried %d item%s from %s" % (n, "" if n == 1 else "s", short_date(src) if src else "nowhere"))
        return
    sys.exit("unknown command %r — try: today help" % cmd)


def cli_daily(args, date_str):
    d = load_daily()
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd in ("list", "ls", "show"):
        for n, it in enumerate(d["items"], 1):
            dn, cnt = daily_state(d, it, date_str)
            if it.get("target"):
                print("%2d. %s %2d/%-3d %s" % (n, bar_text(cnt, it["target"]), cnt, it["target"], it["title"]))
            else:
                print("%2d. [%s] %s" % (n, "✓" if dn else " ", it["title"]))
        if not d["items"]:
            print("no daily tasks — today daily add \"Finish one leetcode problem\"")
        return
    if cmd == "add":
        count = _flag(rest, "--count")
        title = " ".join(rest).strip()
        if not title:
            sys.exit("usage: today daily add TITLE [--count N]")
        target = int(count) if count else guess_target(title)
        d["items"].append({"id": new_id(), "title": title, "target": target})
        save_daily(d)
        print("added  %s%s" % (title, ("  (counter to %d)" % target) if target else ""))
        return
    if not rest:
        sys.exit("usage: today daily %s N" % cmd)
    it = _pick(d["items"], rest[0], "daily task")
    if cmd in ("rm", "remove"):
        d["items"] = [i for i in d["items"] if i["id"] != it["id"]]
    elif cmd == "done":
        daily_set(d, it, date_str, done=True)
    elif cmd == "undo":
        daily_set(d, it, date_str, done=False)
    elif cmd in ("inc", "+"):
        k = int(rest[1]) if len(rest) > 1 else 1
        daily_set(d, it, date_str, count=daily_state(d, it, date_str)[1] + k)
    elif cmd == "set":
        daily_set(d, it, date_str, count=int(rest[1]))
    elif cmd == "edit":
        it["title"] = " ".join(rest[1:]).strip() or it["title"]
    elif cmd == "count":
        it["target"] = int(rest[1]) if len(rest) > 1 and rest[1] != "off" else None
    else:
        sys.exit("unknown daily command %r" % cmd)
    save_daily(d)
    dn, cnt = daily_state(d, it, date_str)
    print("%s  %s%s" % (cmd, it["title"], ("  %d/%d" % (cnt, it["target"])) if it.get("target") else ("  ✓" if dn else "")))


def cli_deadline(args):
    d = load_deadlines()
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd in ("list", "ls", "show"):
        for n, x in enumerate(all_deadlines(d, load_inbox()), 1):
            mark = "✓" if x["done"] else ("●" if x["sev"] in ("soon", "over") else "○")
            src = "" if x["source"] == "manual" else "  (from mail)"
            print("%2d. %s %-28s %-22s %s%s" % (n, mark, clip(x["name"], 28), deadline_when(x),
                                                 "" if x["done"] else x["until"], src))
        return
    if cmd == "add":
        if not rest:
            sys.exit('usage: today deadline add NAME WHEN   e.g. today deadline add "Akuna OA" "2026-10-01 11:27"')
        name = rest[0]
        when = parse_when(" ".join(rest[1:])) if len(rest) > 1 else ""
        if len(rest) > 1 and not when:
            sys.exit("time not understood: %r  (try: 2026-10-01 11:27, Oct 1 11:27am, tomorrow 9pm)" % " ".join(rest[1:]))
        d["items"].append({"id": new_id(), "name": name, "at": when or "", "note": "", "done": False})
        save_deadlines(d)
        print("added  %s  %s" % (name, fmt_when(when) if when else "(no date)"))
        return
    merged = all_deadlines(d, load_inbox())
    x = _pick(merged, rest[0] if rest else "?", "deadline")
    if x["source"] == "inbox":
        inbox = load_inbox()
        for i in inbox["items"]:
            if i["id"] == x["id"]:
                if cmd == "done":
                    i["status"] = "done"
                elif cmd in ("rm", "remove"):
                    i["status"] = "hidden"
                elif cmd == "undo":
                    i["status"] = "open"
        save_inbox(inbox)
    else:
        for i in d["items"]:
            if i["id"] == x["id"]:
                if cmd == "done":
                    i["done"] = True
                elif cmd == "undo":
                    i["done"] = False
        if cmd in ("rm", "remove"):
            d["items"] = [i for i in d["items"] if i["id"] != x["id"]]
        save_deadlines(d)
    print("%s  %s" % (cmd, x["name"]))


def cli_mail(args):
    cfg = load_config()
    cmd = args[0] if args else "list"
    if cmd in ("list", "ls"):
        if not cfg["accounts"]:
            print("no accounts — today mail add you@gmail.com")
        for a in cfg["accounts"]:
            print("  %s  (%s)" % (a, "password saved" if has_password(a) else "NO PASSWORD"))
        return
    if cmd == "add":
        if len(args) < 2 or "@" not in args[1]:
            sys.exit("usage: today mail add you@gmail.com")
        acct = args[1].strip().lower()
        import getpass
        print("Google account: %s" % acct)
        print("Create an app password at https://myaccount.google.com/apppasswords (needs 2-Step Verification),")
        print("then paste it here. Spaces are fine. It is stored in the macOS Keychain (service: %s)." % KEYCHAIN_SERVICE)
        pw = getpass.getpass("App password: ").replace(" ", "").strip()
        if not pw:
            sys.exit("nothing entered")
        print("Testing IMAP login…", end=" ", flush=True)
        try:
            where = add_account(acct, pw)
        except LoginFailed as e:
            print("failed.\n  %s" % e)
            print("  Checks: is IMAP enabled for the account? Is it an app password, not your normal password?")
            print("  For a school Google Workspace account the admin may have disabled app passwords; forwarding")
            print("  that mail to your Gmail is the simplest workaround.")
            sys.exit(1)
        print("ok.")
        print("saved %s (%s). Run: today scan" % (acct, "Keychain" if where == "keychain" else "config.json, mode 600"))
        return
    if cmd in ("rm", "remove"):
        print("removed %s" % remove_account(args[1] if len(args) > 1 else ""))
        return
    sys.exit("usage: today mail [list | add EMAIL | rm EMAIL]")


def cli_scan(args=()):
    cfg = load_config()
    if not cfg["accounts"]:
        sys.exit("no mail accounts yet — today mail add you@gmail.com")
    res = scan_all(cfg, progress=lambda t: print("  " + t), history="--history" in args)
    for a, e in res["errors"].items():
        print("  ! %s: %s" % (a, e))
    print("found %d new relevant message%s" % (res["found"], "" if res["found"] == 1 else "s"))
    t = res["tracker"]
    print("applications: %d total · %d active · %d rejected" % (t["applications"], t["active"], t["rejected"]))
    print_inbox(res["inbox"])


def cli_report(args):
    args = list(args)
    date = _flag(args, "--date")
    if not args:
        sys.exit('usage: today report COMPANY [ROLE] [--date YYYY-MM-DD]   e.g. today report Paylinq "SWE Intern"')
    try:
        r = report_applied(args[0], " ".join(args[1:]), date=date)
    except ValueError as e:
        sys.exit(str(e))
    what = "%s · %s" % (r["company"], r["role"] or "(no role)")
    if not r["counted"]:
        print("already tracked: %s" % what)
        return
    tail = ""
    if r["daily"]:
        c = r["daily"]
        tail = "  (%s %d/%d)" % (c["title"], c["count"], c["target"])
    print("%s: %s%s" % ("recorded" if r["new"] else "counted (already in the tracker from mail)", what, tail))


def cli_apps(args):
    TR = tracker
    tr = TR.load(APP_DIR)
    if not tr["apps"] and not tr["seen"]:
        TR.ingest_items(tr, load_inbox()["items"])
        TR.save(APP_DIR, tr)
    t = TR.totals(tr)
    print("%d applications · %d active (%d waiting, %d in progress) · %d rejected · %d reached interview · %d offer%s"
          % (t["applications"], t["active"], t["waiting"], t["in_progress"], t["rejected"], t["reached_interview"],
             t["offers"], "" if t["offers"] == 1 else "s"))
    rows = [TR.derive(a) for a in tr["apps"]]
    if "--all" not in args:
        rows = [r for r in rows if r["status"] == "active" and r["stage"] != "applied"]
        print("in progress (today apps --all for everything):")
    rows.sort(key=lambda r: r["last"], reverse=True)
    for r in rows:
        tail = r["status_label"] if r["status"] != "active" else ("since " + short_date(r["stage_date"] or r["applied"]))
        print("  %-12s %-18s %-44s %s" % (clip(r["stage_label"], 12), clip(r["company"], 18), clip(r["role"] or "-", 44), tail))


def main(argv):
    args = list(argv)
    date_str = today_str()
    if args and args[0] == "tomorrow":
        date_str = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        args = args[1:] or ["show"]
    elif args and args[0] == "--date" and len(args) > 1:
        date_str = args[1]
        args = args[2:] or ["show"]
    if not args:
        return print_plan(load_plan(date_str), load_daily(), load_deadlines(), load_inbox())
    cmd = args[0]
    if cmd in ("help", "-h", "--help"):
        print(__doc__.strip())
    elif cmd in ("version", "--version"):
        print("today %s" % VERSION)
    elif cmd == "daily":
        cli_daily(args[1:], date_str)
    elif cmd in ("deadline", "deadlines"):
        cli_deadline(args[1:])
    elif cmd == "mail":
        cli_mail(args[1:])
    elif cmd == "scan":
        cli_scan(args[1:])
    elif cmd in ("apps", "applications"):
        cli_apps(args[1:])
    elif cmd == "report":
        cli_report(args[1:])
    elif cmd == "inbox":
        print_inbox(load_inbox(), show_all="--all" in args)
    elif cmd == "where":
        print(APP_DIR)
    else:
        cli_plan(args, date_str)
