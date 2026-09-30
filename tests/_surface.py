"""Where the mail classifier's functions live. The restructure moves them from today.py into mail.py;
tests reach them through here so moving them never means editing tests."""


def mail():
    try:
        import mail as m
        if hasattr(m, "classify"):
            return m
    except ImportError:
        pass
    import today as m
    return m
