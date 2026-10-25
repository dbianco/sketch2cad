"""The edit lease of a plan: a single editor at a time (spec sections 6, 8.1, 9.1; V-12).

``plan.edit_lease`` stores ``{"user_id", "expires_at"}``. A lease is valid while
``now < expires_at``; an expired lease counts as no lease, so anyone with ``edit`` may take
it. Acquiring by the holder of a valid lease renews it and writes no event; any other
acquisition (first, after expiry, or a takeover of an expired lease) writes
``lease.acquired`` with the previous lease, if any, in ``before``. Releasing writes
``lease.released``. Every content edit of a plan requires the caller to hold a valid lease
(:func:`ensure_lease_holder`). All of it runs under a ``FOR UPDATE`` lock on the plan row,
so concurrent acquisitions and edits serialize.

Time comes from :data:`lease_clock`; tests replace it to move time forward.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import Clock, utc_now
from sketch2cad.db.models import Plan, User

logger = logging.getLogger(__name__)

LEASE_TTL = timedelta(minutes=10)
"""Lifetime of a lease from its last acquisition or renewal (spec section 6)."""

V12_MESSAGE = "{holder} está editando este plano. Entra en solo lectura o espera a que termine."
LEASE_REQUIRED_MESSAGE = "Acquire the edit lease of this plan before editing it."
LEASE_REQUIRED_CODE = "lease_required"

lease_clock: Clock = utc_now
"""Clock of every lease decision; tests replace it (``monkeypatch.setattr``)."""


def lease_now() -> datetime:
    """The current time according to :data:`lease_clock`."""
    return lease_clock()


@dataclass(frozen=True)
class Lease:
    """A stored lease: its holder and when it expires."""

    user_id: uuid.UUID
    expires_at: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Lease":
        return cls(uuid.UUID(str(data["user_id"])), datetime.fromisoformat(str(data["expires_at"])))

    def to_json(self) -> dict[str, str]:
        return {"user_id": str(self.user_id), "expires_at": self.expires_at.isoformat()}

    def is_valid(self, now: datetime) -> bool:
        return now < self.expires_at


@dataclass(frozen=True)
class LeaseHolder:
    """A valid lease with the name the UI shows for its holder."""

    user_id: uuid.UUID
    display_name: str
    expires_at: datetime

    def to_json(self) -> dict[str, str]:
        return {
            "user_id": str(self.user_id),
            "display_name": self.display_name,
            # Same form as the API's LeaseOut: UTC with a ``Z`` suffix.
            "expires_at": self.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }


class LeaseConflictError(Exception):
    """The caller does not hold the lease (409).

    ``code`` is ``V-12`` when another user holds a valid lease (``holder`` set), else
    ``lease_required``.
    """

    def __init__(self, message: str, code: str, holder: LeaseHolder | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.holder = holder

    @property
    def extensions(self) -> dict[str, Any]:
        """Problem details members besides ``code``: ``holder`` for V-12."""
        return {} if self.holder is None else {"holder": self.holder.to_json()}


def stored_lease(plan: Plan) -> Lease | None:
    """The lease stored on ``plan``, expired or not."""
    return None if plan.edit_lease is None else Lease.from_json(plan.edit_lease)


def valid_lease(plan: Plan, now: datetime) -> Lease | None:
    """The lease of ``plan`` if it has not expired at ``now``."""
    lease = stored_lease(plan)
    return lease if lease is not None and lease.is_valid(now) else None


def holder_name(user: User | None) -> str:
    """Display name of a lease holder; the username when no display name is set."""
    if user is None:  # pragma: no cover - lease holders are existing users
        return ""
    return user.display_name or user.username


def lease_holder(db: Session, lease: Lease) -> LeaseHolder:
    """``lease`` with its holder's display name."""
    return LeaseHolder(lease.user_id, holder_name(db.get(User, lease.user_id)), lease.expires_at)


def current_holder(db: Session, plan: Plan) -> LeaseHolder | None:
    """The holder of the valid lease of ``plan`` now, or ``None``."""
    lease = valid_lease(plan, lease_now())
    return None if lease is None else lease_holder(db, lease)


def _held_by_other(db: Session, lease: Lease) -> LeaseConflictError:
    holder = lease_holder(db, lease)
    return LeaseConflictError(V12_MESSAGE.format(holder=holder.display_name), "V-12", holder)


def ensure_lease_holder(db: Session, plan: Plan, user: User) -> Lease:
    """The valid lease of ``plan`` if ``user`` holds it, else raise :class:`LeaseConflictError`.

    The caller must hold the plan row lock (``FOR UPDATE``) for the rest of the edit.
    """
    lease = valid_lease(plan, lease_now())
    if lease is None:
        raise LeaseConflictError(LEASE_REQUIRED_MESSAGE, LEASE_REQUIRED_CODE)
    if lease.user_id != user.id:
        raise _held_by_other(db, lease)
    return lease


def _append(
    db: Session,
    plan: Plan,
    user: User,
    kind: EventKind,
    client_ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=plan.current_revision_id,
        entity_id=plan.id,
        before=before,
        after=after,
        client_ip=client_ip,
    )


def acquire_lease(db: Session, plan: Plan, user: User, client_ip: str | None = None) -> LeaseHolder:
    """Give ``user`` the lease of ``plan`` until now + :data:`LEASE_TTL`.

    Locks the plan row. Raises :class:`LeaseConflictError` (V-12) when another user holds a
    valid lease. A renewal by the holder writes no event; otherwise ``lease.acquired`` is
    appended with the previous (expired) lease in ``before``. The caller commits.
    """
    db.refresh(plan, with_for_update=True)
    now = lease_now()
    previous = stored_lease(plan)
    if previous is not None and previous.is_valid(now) and previous.user_id != user.id:
        raise _held_by_other(db, previous)
    lease = Lease(user.id, now + LEASE_TTL)
    plan.edit_lease = lease.to_json()
    db.flush()
    renewal = previous is not None and previous.is_valid(now)
    if renewal:
        logger.info("User %s renewed the lease of plan %s", user.id, plan.id)
    else:
        _append(
            db,
            plan,
            user,
            EventKind.LEASE_ACQUIRED,
            client_ip,
            before=None if previous is None else previous.to_json(),
            after=lease.to_json(),
        )
        logger.info("User %s acquired the lease of plan %s", user.id, plan.id)
    return LeaseHolder(user.id, holder_name(user), lease.expires_at)


def release_lease(db: Session, plan: Plan, user: User, client_ip: str | None = None) -> bool:
    """Clear ``user``'s lease of ``plan`` and append ``lease.released``; whether one was held.

    Locks the plan row. Only the holder releases: a valid lease of another user raises
    :class:`LeaseConflictError` (V-12); no lease, or an expired lease of another user,
    is left as is and returns ``False``. The caller commits.
    """
    db.refresh(plan, with_for_update=True)
    lease = stored_lease(plan)
    if lease is None:
        return False
    if lease.user_id != user.id:
        if lease.is_valid(lease_now()):
            raise _held_by_other(db, lease)
        return False
    plan.edit_lease = None
    db.flush()
    _append(db, plan, user, EventKind.LEASE_RELEASED, client_ip, before=lease.to_json())
    logger.info("User %s released the lease of plan %s", user.id, plan.id)
    return True
