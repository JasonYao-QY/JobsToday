"""The tests never see your real data: everything imported in this process uses a throwaway TODAY_HOME, and
tests that run the app in a subprocess pass their own temporary folder.

The timezone is pinned too. The mail fixtures quote real deadline wording ("Oct 6, 2026 12:46 PM ET"), and
the expected results are the local times that wording means in US Eastern, so without this the suite passes
only on a machine already set to that zone — not in CI, and not for anyone who clones this elsewhere.
"""
import os
import tempfile
import time

os.environ["TODAY_HOME"] = tempfile.mkdtemp(prefix="today-tests-")
os.environ["TZ"] = "America/New_York"
time.tzset()
