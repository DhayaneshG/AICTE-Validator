"""Test environment: scheduler off (tests trigger jobs explicitly); fast retry backoff."""
import os
import shutil
import tempfile

# ONE temp var-dir / DB for the whole session, fixed before any backend module is imported (the engine, storage root and
# crypto keys are module-level singletons, so per-module temp dirs only caused cross-module clobbering).
_SESSION_TMP = tempfile.mkdtemp(prefix="aicte_tests_")
os.environ["AICTE_VAR"] = _SESSION_TMP
os.environ["AICTE_DB"] = f"sqlite:///{_SESSION_TMP}/test.db"
os.environ.setdefault("AICTE_SCHEDULER", "0")
os.environ.setdefault("AICTE_BACKOFF_BASE", "0.001")


def login(c, username="admin", password="Demo@1234"):
    """Log in through the real endpoint; returns the Authorization header dict."""
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SESSION_TMP, ignore_errors=True)
