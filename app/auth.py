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
import threading
import time

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


# ---- password policy ----
# A tiny blocklist of the most-guessed passwords. In production you'd load a larger
# list (e.g. the SecLists / HaveIBeenPwned top-N) — this covers the obvious ones.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyuiop", "letmein1", "11111111", "iloveyou",
    "admin123", "welcome1", "changeme", "passw0rd", "abcd1234",
})


def validate_password_strength(username: str, password: str) -> None:
    """Raise ValueError with a human-readable reason if the password is too weak."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if password.lower() in COMMON_PASSWORDS:
        raise ValueError("password is too common — pick something less guessable")
    if username and username.lower() in password.lower():
        raise ValueError("password must not contain the username")
    if len(set(password)) < 4:
        raise ValueError("password is not varied enough")


def _secret_ok(app_env: str, secret: str) -> bool:
    """A weak/empty AUTH_SECRET_KEY is only tolerated outside production."""
    if app_env == "production":
        return bool(secret) and len(secret) >= 32
    return True


def enforce_secret_policy() -> None:
    s = get_settings()
    if not _secret_ok(s.app_env, s.auth_secret_key):
        raise RuntimeError(
            "AUTH_SECRET_KEY must be set to at least 32 characters when APP_ENV=production"
        )


# ---- password hashing ----
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---- tokens ----
# Two token types: short-lived "access" tokens for API calls and longer-lived
# "refresh" tokens that mint new access tokens. Each carries a unique jti so it
# can be revoked server-side (real logout), unlike a plain stateless JWT.
def _secret() -> str:
    return get_settings().auth_secret_key or _RUNTIME_SECRET


def _make_token(username: str, kind: str, ttl_min: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": username,
        "type": kind,
        "jti": secrets.token_urlsafe(9),
        "iat": now,
        "exp": now + dt.timedelta(minutes=ttl_min),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def create_access_token(username: str) -> str:
    return _make_token(username, "access", get_settings().auth_token_ttl_min)


def create_refresh_token(username: str) -> str:
    return _make_token(username, "refresh", get_settings().auth_refresh_ttl_min)


def decode(token: str) -> dict | None:
    """Return the full payload for a valid, unexpired, correctly-signed token, else None."""
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None


# ---- revocation (real logout) ----
# jti -> expiry timestamp. Pruned on read so it can't grow without bound.
# ponytail: per-process dict; back it with Redis/DB for multi-instance deployments.
_revoked: dict[str, float] = {}
_revoked_lock = threading.Lock()


def revoke(jti: str, exp) -> None:
    if not jti:
        return
    exp_ts = exp.timestamp() if isinstance(exp, dt.datetime) else float(exp)
    with _revoked_lock:
        _revoked[jti] = exp_ts


def revoke_payload(payload: dict) -> None:
    revoke(payload.get("jti", ""), payload.get("exp", 0))


def is_revoked(jti: str) -> bool:
    now = time.time()
    with _revoked_lock:
        for k, e in list(_revoked.items()):
            if e < now:
                _revoked.pop(k, None)
        return jti in _revoked


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
    enforce_secret_policy()  # fail fast in production if the JWT secret is weak
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


def get_current_payload(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Validate a Bearer *access* token and return its payload, or raise 401.

    Rejects refresh tokens (wrong type) and revoked tokens (logged out)."""
    unauth = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if cred is None or (cred.scheme or "").lower() != "bearer":
        raise unauth
    payload = decode(cred.credentials)
    if not payload or payload.get("type") != "access" or not payload.get("sub"):
        raise unauth
    if is_revoked(payload.get("jti", "")):
        raise unauth
    return payload


def get_current_user(payload: dict = Depends(get_current_payload)) -> str:
    """The caller's username (built on the validated access-token payload)."""
    return payload["sub"]
