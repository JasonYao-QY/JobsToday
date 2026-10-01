# today

A one-day scheduler for macOS: a native window (**Today.app**) over a backend of plain Python modules and
JSON files.

It also reads your job-application mail over IMAP and turns it into one record per application, so
assessments, interviews and rejections land on the same page as the rest of the day.

<!-- Add a screenshot of the window here: ![The Today window](docs/window.png) -->

## Why

Tracking an internship search across two inboxes meant assessment deadlines living in email, the day's plan
living somewhere else, and no reliable answer to "how many applications are actually open". This keeps all
three in one place, on one machine, with no account and no server.

Nothing leaves the machine except the IMAP connection to Gmail. There is no web server, no localhost port,
and no network call to anything else.

## Requirements

macOS, and Python 3.9 or newer. The core is standard library only; the window adds
[pywebview](https://pywebview.flowrl.com/), installed into a local `venv/` by the setup script.

## Install

The scripts assume the code lives at `~/.config/today`, alongside its data:

```sh
git clone https://github.com/<you>/today.git ~/.config/today
cd ~/.config/today

sh install-gui.sh     # venv + pywebview, builds ~/Applications/Today.app, opens it at login
cp .env.example .env  # optional local settings; git-ignored
```

`TODAY_HOME=/some/dir today …` points the app at a different data folder. The tests use it.

## The window

- **Plan** — the day's schedule (quick add like `6:10pm Norvia OA 75m`; hover a row for −15/+15, move to the
  next day, edit, delete), Daily (checkboxes and counters), and Deadlines (manual ones plus open
  assessments found in mail). `‹ ›` switch days, and a banner offers unfinished items from the last plan.
- **Inbox** — what the mail scan found, filterable. Click a row to open it in Gmail, mark assessments done,
  hide items.
- **Applications** — one record per application, kept permanently in `applications.json`. Totals, a per-week
  chart, and each application's stage: Applied → Assessment → Assessment done → Interview → Offer, or
  Rejected. Open one to see its emails, move a mis-attached email, merge duplicates, override the stage or
  status, add a note, or delete it so rescans don't bring it back.
- **Mail accounts** — add an account with a Google app password, tested over IMAP and saved to the Keychain.
- Keys: `N` add, `← →` days, `T` today, `⌘1/2/3/4` views, `⌘R` check mail, `Esc` cancel.

## Mail scan

`today mail add EMAIL` asks for a Google **app password** (Google Account → Security → 2-Step Verification →
App passwords) and stores it in the macOS Keychain under the service `today-mail`. The scan connects to
`imap.gmail.com`, searches recent mail with Gmail's query syntax, and classifies what it reads:

- **OA** — assessment invitations (HackerRank, Criteria, HireVue, Canditech, CodeSignal and others), with the
  deadline and duration pulled from the text when present. A later "thanks for completing" from the same
  company marks it done.
- **INTVW** — interview invitations and scheduling requests.
- **REJ** — rejections. A conditional "if you're not selected…" inside an application confirmation does not
  count.
- **NOTE** — other mail with an urgent-looking subject, excluding newsletters.
- **APPLD** — application confirmations, counted but not listed.

An account's first scan reads everything since `history_since` (in `config.json`, changeable on the Mail
accounts page) and can take a few minutes; later scans read only what they haven't seen. Matching an email
to an application is by company first, tolerant of forms like `JPMorganChase` against `JPMorgan Chase`, then
by the role named in the email.

Any Google-hosted address works, including a university Workspace account. If an administrator has app
passwords disabled, forward that mail to Gmail and one account covers both.

## Code layout

One frontend over a backend cut by feature rather than by layer, so each slice owns the minimum it needs.
The Api class is the only bridge; nothing in the backend knows the window exists. Mail credentials and
message bodies reach exactly one module.

```
today_gui.py   the frontend: pywebview window + the Api the page calls
web/           index.html, app.css, app.js — inlined into one page at startup, loaded from memory
plans.py  daily.py  deadlines.py  inbox.py    one feature each, public surface in __all__
mail.py        IMAP, classifier, scan; the only module that touches mail credentials
tracker.py     applications (applications.json)
report.py      "I applied to X", shared by the CLI and the window
background.py  one long-running job at a time for the window (the mail scan)
store.py  timeutil.py  textutil.py  env.py    leaves: depend on nothing
```

Data lives in plain JSON under the data folder, written atomically (temp file, then rename). There is no
database and no locking, which is fine for one person on one machine and is the first thing that would need
to change otherwise.

The page keeps up with changes on disk through `Api.version()`, a cheap signature built from the data
files' modification times that the page polls; when it changes, the page reloads. That also covers edits
made by anything else touching the same folder.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

Standard library only, no network, no mail account needed.

Tests reach the app two ways: a slice's public functions against real JSON in a throwaway `TODAY_HOME`,
and the `Api` the page calls, driven headlessly. Both check behavior from outside the module being tested,
so they keep passing however the code is arranged inside. `tests/fixtures/mail_samples.json` pins the
classifier against realistic message wording; change an expectation only together with the rule change
that justifies it.

## Local settings

`.env` in the data folder holds values specific to one install and is git-ignored. `.env.example` lists the
names. Everything in it is optional, and nothing in it is secret: app passwords live in the Keychain.

## Notes

Built with [Claude](https://claude.com/claude-code). The commit history is one commit per step, with the
test suite green at each one.

## License

MIT. See [LICENSE](LICENSE).
