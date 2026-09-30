"""The tests never see your real data: everything imported in this process uses a throwaway TODAY_HOME, and
tests that run the app in a subprocess pass their own temporary folder."""
import os
import tempfile

os.environ["TODAY_HOME"] = tempfile.mkdtemp(prefix="today-tests-")
