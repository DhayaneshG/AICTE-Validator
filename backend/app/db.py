"""Database engine/session (SQLAlchemy 2.x). SQLite by default; set AICTE_DB to a Postgres URL to switch."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

VAR = Path(os.environ.get("AICTE_VAR", Path(__file__).resolve().parents[2] / "var"))
VAR.mkdir(parents=True, exist_ok=True)
DB_URL = os.environ.get("AICTE_DB", f"sqlite:///{VAR / 'app.db'}")


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DB_URL):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_con, _):
            dbapi_con.execute("PRAGMA foreign_keys=ON")
            dbapi_con.execute("PRAGMA journal_mode=WAL")
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(eng=None):
    from . import models  # noqa: F401  (register tables)
    from ..services import audit
    eng = eng or engine
    Base.metadata.create_all(eng)
    audit.install_append_only_guards(eng)
