"""Account lockout after repeated failed logins, with an injectable clock.

An account is locked while ``now < locked_until``; at exactly ``locked_until`` it is
unlocked again. Failed attempts are counted with a single ``UPDATE ... RETURNING`` so
concurrent failures serialize on the row lock and none is lost.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, null, update
from sqlalchemy.orm import Session

from sketch2cad.db.models import User

LOCKOUT_THRESHOLD = 10
"""Consecutive failed logins that lock the account."""

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware datetime."""


def utc_now() -> datetime:
    """Default clock: the current UTC time."""
    return datetime.now(UTC)


def get_clock() -> Clock:
    """FastAPI dependency returning the clock; tests override it to control time."""
    return utc_now


def is_locked(user: User, now: datetime) -> bool:
    """Return whether ``user`` is locked at ``now`` (locked while ``now < locked_until``)."""
    return user.locked_until is not None and now < user.locked_until


@dataclass(frozen=True)
class FailureResult:
    """State of the account right after a failed attempt was recorded."""

    failed_attempts: int
    locked_until: datetime | None


def register_failure(
    db: Session, user_id: uuid.UUID, now: datetime, lockout_minutes: int
) -> FailureResult:
    """Atomically count a failed login and lock the account when the threshold is reached.

    While a lock is active the counter and ``locked_until`` are left untouched, so failures
    during a lock never extend it. After an expired lock the count starts again at one.
    The caller commits.
    """
    active_lock = User.locked_until > now
    expired_lock = User.locked_until <= now
    next_count = User.failed_attempts + 1
    statement = (
        update(User)
        .where(User.id == user_id)
        .values(
            failed_attempts=case(
                (active_lock, User.failed_attempts),
                (expired_lock, 1),
                else_=next_count,
            ),
            locked_until=case(
                (active_lock, User.locked_until),
                (expired_lock, null()),
                (next_count >= LOCKOUT_THRESHOLD, now + timedelta(minutes=lockout_minutes)),
                else_=null(),
            ),
        )
        .returning(User.failed_attempts, User.locked_until)
        .execution_options(synchronize_session=False)
    )
    row = db.execute(statement).one()
    return FailureResult(failed_attempts=row.failed_attempts, locked_until=row.locked_until)


def reset_failures(user: User) -> None:
    """Clear the failure counter and any lock after a successful login; the caller commits."""
    user.failed_attempts = 0
    user.locked_until = None
