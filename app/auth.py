"""Authentication: a users table (bcrypt-hashed passwords) + signed JWT access tokens.

Kept in its own SQLite database (separate from the DuckDB *data* db) so reseeding
the demo data with `python -m app.db` never touches accounts.

ponytail: SQLite + one table is plenty for auth here; point AUTH_DB_URL at Postgres
if this ever needs to scale past one process.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import secrets

import bcrypt
import jwt
from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import get_settings

log = logging.getLogger("app.auth")

# A random per-process secret is used only when AUTH_SECRET_KEY is unset. It is
# secure (nothing weak is shipped) but tokens do not survive a restart — set
# AUTH_SECRET_KEY in production for stable sessions.
_RUNTIME_SECRET = secrets.token_urlsafe(48)
_ALGO = "HS256"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=lambda: dt.datetime.now(dt.timezone.utc)
    )


_url = get_settings().auth_db_url
_engine = create_engine(
    _url,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
)
_Session = sessionmaker(_engine, expire_on_commit=False)


# ---- password hashing ----
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---- tokens ----
def _secret() -> str:
    return get_settings().auth_secret_key or _RUNTIME_SECRET


def create_token(username: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + dt.timedelta(minutes=get_settings().auth_token_ttl_min),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_token(token: str) -> str | None:
    """Return the username for a valid, unexpired token, else None."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[_ALGO])
        sub = data.get("sub")
        return sub if isinstance(sub, str) else None
    except jwt.PyJWTError:
        return None


# ---- user store ----
def create_user(username: str, password: str) -> str:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username and password are required")
    with _Session() as s:
        if s.scalar(select(User).where(User.username == username)):
            raise ValueError("username already taken")
        s.add(User(username=username, password_hash=hash_password(password)))
        s.commit()
    return username


def authenticate(username: str, password: str) -> str | None:
    with _Session() as s:
        user = s.scalar(select(User).where(User.username == (username or "").strip()))
    if user and user.is_active and verify_password(password, user.password_hash):
        return user.username
    return None


def init_auth_db() -> None:
    """Create the table (idempotent) and seed the demo account. Safe to call repeatedly."""
    if _url.startswith("sqlite:///"):
        path = _url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Base.metadata.create_all(_engine)

    s = get_settings()
    if s.auth_seed_demo:
        try:
            create_user(s.auth_demo_user, s.auth_demo_password)
            log.info("seeded demo account '%s'", s.auth_demo_user)
        except ValueError:
            pass  # already exists
    if not s.auth_secret_key:
        log.warning(
            "AUTH_SECRET_KEY is not set — using a random per-process secret; "
            "tokens will not survive a restart. Set AUTH_SECRET_KEY in production."
        )


# ---- FastAPI dependency ----
# Imported lazily-friendly: only pulls fastapi at import time (already a dep).
from fastapi import Depends, HTTPException, status  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # noqa: E402

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Resolve the caller's username from a Bearer token, or raise 401."""
    unauth = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if cred is None or (cred.scheme or "").lower() != "bearer":
        raise unauth
    username = decode_token(cred.credentials)
    if not username:
        raise unauth
    return username
