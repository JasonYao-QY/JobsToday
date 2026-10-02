# -*- coding: utf-8 -*-
"""Local settings that belong to one person's install, not to the source. Leaf module.

Values come from the real environment first, then from `.env` in the data folder (APP_DIR). `.env` is
git-ignored; `.env.example` in the repository lists the names with placeholder values.

Nothing here is required: every getter returns an empty default when the file is absent, so a fresh clone
runs with no setup.

Public: get, account_hints, name_tokens
"""

import os

from store import APP_DIR

ENV_FILE = os.path.join(APP_DIR, ".env")


def _parse(text):
    """KEY=value per line. Blank lines and # comments are skipped, as is a leading `export `. Surrounding
    single or double quotes are stripped. A malformed line is ignored rather than raising."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _file_values():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            return _parse(f.read())
    except OSError:
        return {}


def get(name, default=""):
    """The real environment wins, so a one-off `TODAY_X=... today ...` overrides the file."""
    if name in os.environ:
        return os.environ[name]
    return _file_values().get(name, default)


def _list(name):
    raw = get(name, "")
    return [v.strip() for v in raw.replace(";", ",").split(",") if v.strip()]


def browser():
    """The browser links from the window open in, as a macOS application name ("Google Chrome"). Empty
    means the system default, which is what `open <url>` already does. -> str"""
    return get("TODAY_BROWSER", "").strip()


def account_hints():
    """Addresses offered as one-click fills on the Mail accounts page. -> list of str, possibly empty."""
    return _list("TODAY_ACCOUNT_HINTS")


def name_tokens():
    """Parts of your own name, lowercased, as they appear at the end of a mail subject
    ("… - Data Scientist Intern - Ada Lovelace"). The role extractor stops there instead of swallowing it.
    -> list of str, possibly empty."""
    return [t.lower() for t in _list("TODAY_NAME_TOKENS")]
