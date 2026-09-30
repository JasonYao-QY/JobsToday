# -*- coding: utf-8 -*-
"""Two tiny text helpers shared by the CLI, the inbox summary and the mail scan. Leaf module.

Public: clip(s, n) shortens to n characters with an ellipsis; plural(n, word) -> "1 item" / "2 items".
"""


def clip(s, n):
    s = str(s)
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")
