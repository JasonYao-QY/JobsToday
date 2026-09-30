# today

A one-day scheduler, as a Mac window (**Today.app**) and as the `today` command. Both read and write the same
files in this folder, so an edit in one shows up in the other within a few seconds.

## The window

Set up once with `sh ~/.config/today/install-gui.sh`. It creates `venv/` (Python + pywebview), builds
`~/Applications/Today.app`, and points the login item at the window instead of Terminal.

- **Plan**: schedule for the day (quick add like `6:10pm Norvia OA 75m`; hover a row for −15/+15, move to the
  next day, edit, delete), Daily (checkboxes and counters), Deadlines (manual ones + open OAs from mail).
  ‹ › switch days; a carry-over banner offers unfinished items from the last plan.
- **Inbox**: what Check mail found, filterable; click a row to open it in Gmail; mark OAs done, hide items.
- **Applications**: one record per application, built from your mail and kept permanently in
  `applications.json`. Totals (applications, in progress, reached assessment / interview, offers, rejections),
  a per-week chart, and each application's stage: Applied → Assessment → Assessment done → Interview → Offer,
  or Rejected. Click one to see its emails (each opens in Gmail), fix a mismatched email ("Move to…"), merge
  duplicates, override stage/status (e.g. Withdrawn), add a note, or delete it (it won't come back on rescans).
  "check" marks an email that was matched by guess, usually a rejection that doesn't name the role when you
  applied to several roles at that company.
- **Mail accounts**: add an account with a Google app password (tested over IMAP, saved in Keychain).
- Keys: `N` add, `← →` days, `T` today, `⌘1/2/3/4` views, `⌘R` check mail, `Esc` cancel.

How the application history is built: an account's first Check mail reads everything since `history_since`
(config.json, default 2026-06-01; change it on the Mail accounts page) and can take a few minutes. Later checks
read only mail they haven't seen. Rescan history (Applications page, or `today scan --history`) re-reads it all;
manual edits are kept. Matching is by company (tolerant of "JPMorganChase"/"JPMorgan Ridgeline", "LTC"/"Chicago Trading
Company") and then by the role named in the email.

Files: `today_gui.py` (window + bridge to today.py), `tracker.py` (applications), `applications.json`, `gui.html` (the page, loaded from memory: no web server,
no port), `icon.png`. Errors go to `/tmp/today-gui.log`; `TODAY_DEBUG=1` enables the web inspector.
Back to a Terminal window at login: `sh ~/.config/today/install-login-item.sh`.

## The command

```
today                 interactive view (schedule · daily · inbox)
today show            plain-text version of the same
today add 6:10pm "Norvia HackerRank" --min 75 --note "one sitting" --urgent
today done 3 | undo 3 | rm 3 | move 3 7pm | edit 3 "title" | note 3 "text"
today carry           bring unfinished items over from the previous plan
today tomorrow add 7pm "StoneRidge OA"      (any plan command, against tomorrow)
today daily add "Apply for 20 jobs"        a number in the title makes it a counter
today daily inc 2 5                         +5 on daily task #2
today deadline add "Akuna OA" "2026-10-01 11:27"
today mail add you@gmail.com               save an app password (Keychain) for the mail scan
today scan                                  scan mail → inbox (OAs with deadlines, interviews, rejections, flagged mail)
today inbox [--all]
```

## Keys in the interactive view

| key | schedule | daily | inbox |
|---|---|---|---|
| `tab` / `1` `2` `3` | switch panel | | |
| `↑` `↓` `j` `k` | move | move | move |
| `space` / `enter` | toggle done | toggle done | **enter** opens the mail in Gmail |
| `a` | add (`6:10pm Title`) | add (a number → counter) | |
| `e` / `n` | edit title / note | edit title | |
| `t` | set time, optionally minutes: `6:10pm 75` | | |
| `+` `-` | shift time ±15 min | counter ±1 | |
| `x` | remove (asks) | remove (asks) | hide |
| `m` | | | mark assessment done / undone |
| `c` | carry unfinished items from the previous plan | | |
| `s` | scan mail (all panels) | | |
| `?` / `q` | help / quit | | |

At midnight the view rolls over to the new day and offers to carry unfinished items.

## Files

- `today.py` — the app. `~/.local/bin/today` is a two-line launcher for it.
- `plans/YYYY-MM-DD.json` — one plan per day. `daily.json` — recurring tasks and a per-day log.
- `deadlines.json` — manual deadlines. Open assessments found in mail show up alongside them automatically.
- `inbox.json` — what the mail scan found. Items are keyed by Message-ID; hidden/done status survives rescans.
- `config.json` — mail accounts (`accounts`), optional IMAP hosts (`hosts`), `scan_days` (30).
- `open-at-login.applescript` + `com.today.scheduler.plist` — the login item (see below).

## Mail scan

`today mail add EMAIL` asks for a Google **app password** (Google Account → Security → 2-Step Verification →
App passwords) and stores it in the macOS Keychain under service `today-mail`. The scan connects over IMAP to
`imap.gmail.com`, searches the last 30 days with Gmail's own query syntax, reads the matching messages and classifies them:

- **OA** — assessment invitations (HackerRank, Criteria, HireVue, Canditech, CodeSignal…) with the deadline and duration
  pulled from the text when present. A later "thanks for completing" mail from the same company marks it done.
- **INTVW** — interview invitations and scheduling requests.
- **REJ** — rejections (phrases like "other candidates", "not moving forward"; a conditional "if you're not selected…"
  inside an application confirmation does not count).
- **NOTE** — other mail with an urgent-looking subject (action required, deadline, expires…), excluding newsletters.
- **APPLD** — application confirmations (counted, not listed).

Both accounts are Google-hosted, so the same setup applies to `you@example.edu` — if CMU's Workspace admin
has app passwords disabled, forward that mail to Gmail instead and one account covers both.

The first inbox was seeded by Claude from a Gmail read on 2026-09-28; the first successful scan of an account replaces its
seeded items.

## Login item

`~/Library/LaunchAgents/com.today.scheduler.plist` runs `open-at-login.applescript` at login, which opens a Terminal window
running `today`. Install/refresh with `sh ~/.config/today/install-login-item.sh`. Remove with
`launchctl bootout gui/$(id -u)/com.today.scheduler; rm ~/Library/LaunchAgents/com.today.scheduler.plist`.

`TODAY_HOME=/some/dir today …` points the app at a different data folder (used by the tests).
