"""Background scheduler (APScheduler). Jobs are registered here; tests disable it (AICTE_SCHEDULER=0) and call the job functions directly."""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("aicte.scheduler")
_sched: BackgroundScheduler | None = None


def retry_job() -> None:
    from ..app.db import SessionLocal
    from . import verification
    db = SessionLocal()
    try:
        r = verification.retry_unresolved(db)
        if r["unresolved_before"]:
            log.info("verification retry: %s", r)
    except Exception:  # never let a job crash the scheduler thread
        log.exception("verification retry job failed")
        db.rollback()
    finally:
        db.close()


def purge_job() -> None:
    from ..app.db import SessionLocal
    from . import retention
    db = SessionLocal()
    try:
        r = retention.purge_expired(db)
        db.commit()
        if r["purged"]:
            log.info("retention purge: %s documents", r["purged"])
    except Exception:
        log.exception("retention purge job failed")
        db.rollback()
    finally:
        db.close()


def alert_job() -> None:
    from ..app.db import SessionLocal
    from . import alerts
    db = SessionLocal()
    try:
        r = alerts.evaluate(db)
        db.commit()
        if r["raised"] or r["escalated"] or r["resolved"]:
            log.info("alert engine: %s", r)
    except Exception:
        log.exception("alert job failed")
        db.rollback()
    finally:
        db.close()


JOBS: list[tuple[str, callable, str, float]] = [  # (id, function, env var for minutes, default minutes)
    ("verification_retry", retry_job, "AICTE_RETRY_MINUTES", 10.0),
    ("alert_engine", alert_job, "AICTE_ALERT_MINUTES", 5.0),
    ("retention_purge", purge_job, "AICTE_PURGE_MINUTES", 1440.0),
]


def register_job(job_id: str, fn, env_var: str, default_minutes: float) -> None:
    JOBS.append((job_id, fn, env_var, default_minutes))


def start() -> BackgroundScheduler | None:
    global _sched
    if os.environ.get("AICTE_SCHEDULER", "1") == "0" or _sched is not None:
        return _sched
    s = BackgroundScheduler(timezone="UTC")
    for job_id, fn, env, default in JOBS:
        s.add_job(fn, "interval", minutes=float(os.environ.get(env, default)), id=job_id, replace_existing=True, max_instances=1, coalesce=True)
    s.start()
    _sched = s
    return s


def shutdown() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None
