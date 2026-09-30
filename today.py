#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""today — a one-day scheduler. Entry point for the `today` command (see cli.py for usage)
and the core the Today window (today_gui.py) imports.
"""

import sys

# Re-exports while the restructure is in progress: today_gui.py and cli.py still use today.<name>.
# Removed once callers import the feature modules directly.
from mail import (  # noqa: F401  (re-exported during the restructure)
    APPLIED_PHRASES,
    APPLIED_WEAK,
    ATS_WORDS,
    CAP,
    CODE_WORDS,
    COMPANY_BODY_PATTERNS,
    COMPANY_SUBJECT_PATTERNS,
    CONDITIONAL,
    CONFIG_FILE,
    CONNECTOR_WORDS,
    DATE_PATS,
    DUE_ANCHORS,
    GENERIC_NAME_WORDS,
    GMAIL_TERMS,
    IMPORTANT_SUBJECT,
    INTERVIEW_NOT,
    INTERVIEW_PHRASES,
    JOB_EVIDENCE,
    JOB_WORDS,
    KEYCHAIN_SERVICE,
    MONTH_PAT,
    OA_DONE,
    OA_WORDS,
    OFFER_PHRASES,
    PLACE_WORDS,
    PLATFORM_DOMAINS,
    PLATFORM_WORDS,
    REJECT_STRONG,
    REJECT_WEAK,
    ROLE_WORDS,
    SKIP_DOMAINS,
    TIME_PAT,
    TZ_FIXED,
    TZ_ZONE,
    WORD_NUM,
    _chunks,
    _parse_date_at,
    _parse_time_at,
    _security,
    _to_local,
    _tracker,
    _us_dst,
    body_text,
    classify,
    clean_subject,
    company_from,
    decode_hdr,
    extract_due,
    extract_minutes,
    fetch_bodies,
    fetch_headers,
    html_to_text,
    imap_quote,
    imap_search,
    is_interview,
    job_context,
    load_config,
    make_item,
    msg_date,
    msg_key,
    password_del,
    password_get,
    password_set,
    phrase_hits,
    platform_of,
    platform_words,
    plausible_company,
    save_config,
    scan_account,
    scan_all,
    tidy_company,
)
from deadlines import (  # noqa: F401  (re-exported during the restructure)
    DEADLINES_FILE,
    all_deadlines,
    deadline_when,
    load_deadlines,
    save_deadlines,
)
from inbox import (  # noqa: F401  (re-exported during the restructure)
    INBOX_FILE,
    assessment_deadlines,
    KIND_LABEL,
    _inbox_group,
    inbox_counts,
    inbox_summary,
    inbox_visible,
    load_inbox,
    merge_inbox,
    save_inbox,
)
from daily import (  # noqa: F401  (re-exported during the restructure)
    DAILY_FILE,
    daily_set,
    daily_state,
    guess_target,
    load_daily,
    save_daily,
)
from plans import (  # noqa: F401  (re-exported during the restructure)
    PLANS_DIR,
    add_item,
    carry_candidates,
    carry_over,
    load_plan,
    plan_dates,
    plan_path,
    save_plan,
    sorted_items,
)
from textutil import (  # noqa: F401  (re-exported during the restructure)
    clip,
    plural,
)
from store import (  # noqa: F401  (re-exported during the restructure)
    APP_DIR,
    load_json,
    new_id,
    save_json,
)
from timeutil import (  # noqa: F401  (re-exported during the restructure)
    DOW,
    MON3,
    MONTHS,
    TIME_RE,
    WHEN_FORMATS,
    _span,
    add_minutes,
    fmt_minutes,
    fmt_time,
    fmt_when,
    human_date,
    now,
    parse_time,
    parse_when,
    short_date,
    split_time_prefix,
    to_date,
    today_str,
    until,
    when_dt,
)


if __name__ == "__main__":
    from cli import main
    try:
        main(sys.argv[1:])
    except BrokenPipeError:
        pass
