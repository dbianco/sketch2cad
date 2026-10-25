"""Server-side sessions; only the SHA-256 of the session token is ever stored."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.db.models import User, UserSession

TOKEN_BYTES = 32


@dataclass(frozen=True)
class IssuedSession:
    """A freshly created session together with its raw token (never persisted)."""

    token: str
    record: UserSession


def hash_token(token: str) -> str:
    """Return the hex SHA-256 digest of ``token``, as stored in ``session.token_sha256``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Session, user: User, ttl_minutes: int, now: datetime | None = None
) -> IssuedSession:
    """Create a session for ``user`` expiring after ``ttl_minutes``; the caller commits."""
    issued_at = now or datetime.now(UTC)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    record = UserSession(
        user_id=user.id,
        token_sha256=hash_token(token),
        csrf_token=secrets.token_urlsafe(TOKEN_BYTES),
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=ttl_minutes),
    )
    db.add(record)
    db.flush()
    return IssuedSession(token=token, record=record)


def find_active_session(db: Session, token: str, now: datetime | None = None) -> UserSession | None:
    """Return the session for ``token`` unless it is unknown, revoked or expired."""
    current = now or datetime.now(UTC)
    statement = select(UserSession).where(
        UserSession.token_sha256 == hash_token(token),
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > current,
    )
    return db.scalars(statement).one_or_none()


def revoke_session(db: Session, record: UserSession, now: datetime | None = None) -> None:
    """Mark ``record`` as revoked; the caller commits."""
    record.revoked_at = now or datetime.now(UTC)
    db.flush()
