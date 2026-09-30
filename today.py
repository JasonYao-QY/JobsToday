#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""today — a one-day scheduler. This file is only the entry point of the `today` command; see cli.py for usage.

Feature modules: plans, daily, deadlines, inbox, mail (the only one with mail credentials), tracker
(applications), report (reporting an application). Leaves: store, timeutil, textutil. The window is
today_gui.py over the same modules.
"""

import sys

from cli import main

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:
        pass
