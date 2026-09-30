# -*- coding: utf-8 -*-
"""A long-running job the window runs off the UI thread (the mail scan now; fetching job pages later), with
progress pushed to the page. Knows nothing about what the job does or about pywebview.

    task = BackgroundTask("scan", push, "onScan")      # push(event, payload) delivers window.onScan(payload)
    task.start(work, *args)   # runs work(progress, *args) in a thread; False if one is already running
    task.running              # for the page's polling

work() returns the final payload; BackgroundTask adds "done": True and pushes it. If work() raises, the
payload is {"done": True, "errors": {name: message}} and on_error(exc, traceback_text) is called.
"""

import threading
import traceback


class BackgroundTask(object):
    def __init__(self, name, push, event, on_error=None):
        self.name = name
        self._push = push
        self._event = event
        self._on_error = on_error
        self._lock = threading.Lock()
        self._running = False

    @property
    def running(self):
        return self._running

    def progress(self, text):
        self._push(self._event, {"progress": text})

    def start(self, work, *args):
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, args=(work, args), daemon=True).start()
        return True

    def _run(self, work, args):
        try:
            out = dict(work(self.progress, *args) or {})
        except Exception as e:
            if self._on_error:
                self._on_error(e, traceback.format_exc())
            out = {"errors": {self.name: str(e)}}
        finally:
            self._running = False
        out["done"] = True
        self._push(self._event, out)
