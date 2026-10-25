from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...services import audit, retention
from .. import auth
from .. import models as M
from ..auth import Actor, get_actor, require
from ..db import get_db

router = APIRouter()


class Login(BaseModel):
    username: str
    password: str


def _me(a: Actor) -> dict:
    return {"username": a.username, "role": a.role, "institution_id": a.institution_id, "region": a.region, "full_name": a.full_name}


@router.post("/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    name = body.username.strip()[:64]
    if auth.locked(name):
        audit.log(db, name, "auth.login_blocked", "user", name, {"reason": "too many failed attempts"}, None)
        db.commit()
        raise HTTPException(429, "too many failed attempts; try again in a few minutes")
    u = db.execute(select(M.User).where(M.User.username == name)).scalar_one_or_none()
    ok = u is not None and u.active and auth.verify_password(body.password, u.password_hash)
    if not ok:
        auth.record_failure(name)
        audit.log(db, name, "auth.login_failed", "user", name, {}, None)
        db.commit()
        raise HTTPException(401, "invalid username or password")
    auth.clear_failures(name)
    audit.log(db, u.username, "auth.login", "user", u.username, {}, u.role)
    db.commit()
    return {"access_token": auth.make_token(u), "token_type": "bearer", "user": _me(Actor(u.username, u.role, u.institution_id, u.region, u.id, u.full_name))}


@router.get("/auth/me")
def me(actor: Actor = Depends(get_actor)):
    return _me(actor)


@router.get("/users")
def users(db: Session = Depends(get_db), actor: Actor = Depends(require("admin_senior"))):
    return [{"username": u.username, "role": u.role, "full_name": u.full_name, "institution_id": u.institution_id, "region": u.region, "active": u.active}
            for u in db.execute(select(M.User).order_by(M.User.role, M.User.username)).scalars()]


@router.post("/admin/purge")
def purge(dry_run: bool = False, older_than_days: int | None = None, db: Session = Depends(get_db), actor: Actor = Depends(require("admin_senior"))):
    r = retention.purge_expired(db, actor.username, actor.role, older_than_days, dry_run)
    db.commit()
    return r
