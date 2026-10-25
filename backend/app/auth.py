"""Authentication (JWT bearer + bcrypt) and role-based access control.

Roles: institution_user | scrutiny_officer | regional_officer | admin_senior.
Row isolation: institution_user -> own institution only; scrutiny/regional officers -> own region only; admin_senior -> all.
Out-of-scope objects answer 404 (not 403) so identifiers cannot be enumerated; a wrong ROLE answers 403.
The user row is re-read on every request, so deactivation or a role change takes effect immediately.

DEMO NOTE: the JWT secret comes from AICTE_JWT_SECRET, or a dev secret generated once into var/dev_jwt.key.
Production needs a managed secret, short-lived tokens with refresh/revocation, MFA and SSO - none of which exist here.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as M
from .db import VAR, get_db

log = logging.getLogger("aicte.auth")
ROLES = ("institution_user", "scrutiny_officer", "regional_officer", "admin_senior")
STAFF = ("scrutiny_officer", "regional_officer", "admin_senior")
ALGO = "HS256"
TOKEN_HOURS = float(os.environ.get("AICTE_TOKEN_HOURS", 8))
MAX_FAILS, LOCK_SECONDS = 5, 300

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)
_fails: dict[str, list[float]] = {}  # username -> timestamps of recent failures (in-memory demo lockout)


@dataclass
class Actor:
    username: str
    role: str
    institution_id: Optional[str] = None
    region: Optional[str] = None
    user_id: Optional[int] = None
    full_name: Optional[str] = None


def _secret() -> str:
    env = os.environ.get("AICTE_JWT_SECRET")
    if env:
        return env
    f = Path(VAR) / "dev_jwt.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        os.chmod(f, 0o600)
        log.warning("AICTE_JWT_SECRET not set: generated DEV secret at %s", f)
    return f.read_text().strip()


def hash_password(pw: str) -> str:
    return _pwd.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    try:
        return _pwd.verify(pw, h)
    except Exception:
        return False


def make_token(u: M.User) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode({"sub": u.username, "uid": u.id, "role": u.role, "iat": now, "exp": now + dt.timedelta(hours=TOKEN_HOURS)}, _secret(), algorithm=ALGO)


def locked(username: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(username, []) if now - t < LOCK_SECONDS]
    _fails[username] = recent
    return len(recent) >= MAX_FAILS


def record_failure(username: str) -> None:
    _fails.setdefault(username, []).append(time.time())


def clear_failures(username: str) -> None:
    _fails.pop(username, None)


def _unauth(msg: str) -> HTTPException:
    return HTTPException(401, msg, headers={"WWW-Authenticate": "Bearer"})


def get_actor(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer), db: Session = Depends(get_db)) -> Actor:
    if creds is None or not creds.credentials:
        raise _unauth("authentication required")
    try:
        p = jwt.decode(creds.credentials, _secret(), algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise _unauth("token expired")
    except jwt.PyJWTError:
        raise _unauth("invalid token")
    u = db.execute(select(M.User).where(M.User.username == p.get("sub"))).scalar_one_or_none()
    if u is None or not u.active:
        raise _unauth("user unknown or disabled")
    return Actor(u.username, u.role, u.institution_id, u.region, u.id, u.full_name)


def require(*roles: str):
    allowed = roles or ROLES

    def dep(actor: Actor = Depends(get_actor)) -> Actor:
        if actor.role not in allowed:
            raise HTTPException(403, f"role '{actor.role}' is not permitted to do this")
        return actor
    return dep


# ---------------------------------------------------------------- row-level scope
def in_scope(actor: Actor, application_region: str, institution_id: str) -> bool:
    if actor.role == "admin_senior":
        return True
    if actor.role == "institution_user":
        return actor.institution_id is not None and institution_id == actor.institution_id
    return actor.region is not None and application_region == actor.region  # scrutiny_officer / regional_officer


def scope_applications(q, actor: Actor):
    """Restrict a SELECT over Application to the actor's rows."""
    if actor.role == "admin_senior":
        return q
    if actor.role == "institution_user":
        return q.where(M.Application.institution_id == (actor.institution_id or "\0"))
    return q.where(M.Application.region == (actor.region or "\0"))


def check_application(actor: Actor, app: M.Application) -> M.Application:
    if not in_scope(actor, app.region, app.institution_id):
        raise HTTPException(404, "application not found")
    return app
