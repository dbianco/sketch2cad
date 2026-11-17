"""Server-side sessions; only the SHA-256 of the session token is ever stored.

Logins are audited on the global auth chain (docs/audit.md): ``auth.login`` names the
session by its id and ``auth.failed`` names the attempt by its cause. Neither event ever
carries the submitted password, the session token or any cookie value.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.audit import EventKind, append_event
from sketch2cad.db.models import User, UserSession

TOKEN_BYTES = 32


class LoginFailureCause(StrEnum):
    """Why a login attempt failed, as recorded in ``auth.failed``."""

    UNKNOWN_USER = "unknown_user"
    INACTIVE_USER = "inactive_user"
    ACCOUNT_LOCKED = "account_locked"
    INVALID_CREDENTIALS = "invalid_credentials"


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


def hash_username(username: str) -> str:
    """Hex SHA-256 of the lower-cased ``username``, recorded instead of an unknown name."""
    return hashlib.sha256(username.lower().encode("utf-8")).hexdigest()


def record_login(
    db: Session, user: User, record: UserSession, client_ip: str | None, now: datetime
) -> None:
    """Append ``auth.login`` for the new session ``record``; the caller commits."""
    append_event(
        db,
        case_file_id=None,
        kind=EventKind.AUTH_LOGIN,
        actor_id=user.id,
        actor_role=user.role.value,
        after={"session_id": str(record.id)},
        client_ip=client_ip,
        occurred_at=now,
    )


def record_login_failure(
    db: Session,
    cause: LoginFailureCause,
    *,
    user: User | None,
    username: str,
    client_ip: str | None,
    now: datetime,
    failed_attempts: int | None = None,
    locked_until: datetime | None = None,
) -> None:
    """Append ``auth.failed``; the caller commits so the event outlives the failed request.

    A known user is the actor. An unknown one leaves the actor empty and is named only by
    :func:`hash_username`, so the event does not store an arbitrary submitted name.
    """
    after: dict[str, Any] = {"cause": cause.value}
    actor_id: uuid.UUID | None = None
    actor_role: str | None = None
    if user is None:
        after["username_sha256"] = hash_username(username)
    else:
        actor_id, actor_role = user.id, user.role.value
    if failed_attempts is not None:
        after["failed_attempts"] = failed_attempts
    if locked_until is not None:
        after["locked_until"] = locked_until.astimezone(UTC).isoformat()
    append_event(
        db,
        case_file_id=None,
        kind=EventKind.AUTH_FAILED,
        actor_id=actor_id,
        actor_role=actor_role,
        after=after,
        client_ip=client_ip,
        occurred_at=now,
    )
