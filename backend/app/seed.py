"""Seed the database from synthetic data (MOCK records + 40 synthetic applications) through the normal service functions.

    python -m backend.app.seed [--reset]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from sqlalchemy import select

from ..services import processing as P
from ..services.clock import clock
from . import models as M
from .db import SessionLocal, init_db

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
RECORDS = ROOT / "backend" / "mock" / "records.json"
SEED_ACTOR = "system.seed"


def seed_institutions(db) -> int:
    n = 0
    for r in json.loads(RECORDS.read_text())["institutions"]:
        if db.get(M.Institution, r["institution_id"]) is None:
            db.add(M.Institution(id=r["institution_id"], name=r["name"], region=r["region"], state=r.get("state"), city=r.get("city"),
                                 approved_intake=r.get("approved_intake")))
            n += 1
    db.flush()
    return n


DEMO_PASSWORD = "Demo@1234"  # synthetic demo accounts only


def seed_users(db, institutions: list[str] | None = None, password: str = DEMO_PASSWORD) -> int:
    """Demo accounts (all synthetic): admin, regional + scrutiny officer per region, two institution users.
    The two institution users belong to the first two DIFFERENT institutions that have applications (else the first two institutions)."""
    from .auth import hash_password
    pw = hash_password(password)
    if institutions is None:
        ids = [i for i in dict.fromkeys(db.execute(select(M.Application.institution_id).order_by(M.Application.id)).scalars().all())][:2]
        if len(ids) < 2:
            ids = list(db.execute(select(M.Institution.id).order_by(M.Institution.id).limit(2)).scalars())
        institutions = ids
    want = [("admin", "admin_senior", "AICTE Senior Administrator (demo)", None, None)]
    for reg in sorted(set(db.execute(select(M.Institution.region)).scalars())):
        want.append((f"regional.{reg.lower()}", "regional_officer", f"{reg} Regional Officer (demo)", None, reg))
        want.append((f"scrutiny.{reg.lower()}", "scrutiny_officer", f"{reg} Scrutiny Officer (demo)", None, reg))
    for tag, inst_id in zip("ab", institutions):
        inst = db.get(M.Institution, inst_id)
        want.append((f"inst.{tag}", "institution_user", f"{inst.name} (demo user)", inst_id, inst.region))
    n = 0
    for username, role, full, inst_id, reg in want:
        if db.execute(select(M.User).where(M.User.username == username)).scalar_one_or_none() is None:
            db.add(M.User(username=username, password_hash=pw, role=role, full_name=full, institution_id=inst_id, region=reg, active=True))
            n += 1
    db.flush()
    return n


def seed_applications(db, limit: int | None = None) -> int:
    labels = json.loads((SYN / "labels.json").read_text())["applications"]
    n = 0
    for a in sorted(labels, key=lambda x: x["application_id"])[:limit]:
        if db.get(M.Application, a["application_id"]) is not None:
            continue
        app = P.create_application(db, a["institution_id"], a["category"], dt.date.fromisoformat(a["submission_date"]), SEED_ACTOR,
                                   "system", app_id=a["application_id"])
        for d in a["documents"]:
            P.add_document(db, app, d["filename"], (SYN / "applications" / a["application_id"] / d["filename"]).read_bytes(), SEED_ACTOR, "system")
        P.run_pipeline(db, app, "initial", SEED_ACTOR, "system")
        db.commit()
        n += 1
    return n


def main(argv: list[str]) -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.execute(select(M.Application.id).limit(1)).first() and "--reset" not in argv:
            print("database already seeded (use --reset after deleting the db file)")
            print("users:", seed_users(db))  # idempotent: adds demo accounts to a DB seeded before M9
            db.commit()
            return
        print("institutions:", seed_institutions(db))
        db.commit()
        print("applications:", seed_applications(db))
        db.commit()
        print("users:", seed_users(db))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
