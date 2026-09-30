# -*- coding: utf-8 -*-
"""Where `today` keeps its data, and how a JSON file is read and written. Leaf module.

Public: APP_DIR (the data folder; TODAY_HOME overrides it), load_json, save_json (atomic: temp file + rename),
new_id. Each feature names its own files under APP_DIR; this module knows no feature's data.
"""

import json
import os
import uuid


APP_DIR = os.environ.get("TODAY_HOME") or os.path.join(os.path.expanduser("~"), ".config", "today")


def new_id():
    return uuid.uuid4().hex[:8]


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
