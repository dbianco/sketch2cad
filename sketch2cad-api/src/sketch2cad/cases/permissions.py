"""Per case file authorization, membership management and ``access.denied`` recording.

The creator of a case file holds every permission; any other user holds exactly the
permissions of their ``case_member`` row. A user with neither is not a member and holds no
permission; a denial is reported as 403 whether or not the user is a member. The ``admin``
role grants no case permission (spec section 3) but may add members to any case file, as
may its creator.
"""

import logging
import uuid
from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.audit.chain import EventKind, append_event, lock_case_file
from sketch2cad.db.models import CaseFile, CaseMember, User, UserRole

logger = logging.getLogger(__name__)

MANAGE_MEMBERS = "manage_members"
"""Name recorded in ``access.denied`` when a user may not add members."""


class CasePermission(StrEnum):
    """Permissions stored in ``case_member.permissions``."""

    READ = "read"
    EDIT = "edit"
    DELIVER = "deliver"


class UnknownUserError(LookupError):
    """The user to add as member does not exist."""


class MemberExistsError(ValueError):
    """The user is already a member of the case file."""


def normalize_permissions(permissions: Iterable[str]) -> list[str]:
    """Deduplicate ``permissions`` in the canonical ``read, edit, deliver`` order."""
    wanted = {CasePermission(value) for value in permissions}
    return [permission.value for permission in CasePermission if permission in wanted]


def find_case(db: Session, case_id: uuid.UUID) -> CaseFile | None:
    """Return the case file ``case_id`` regardless of membership, or ``None``."""
    return db.get(CaseFile, case_id)


def case_permissions(db: Session, user_id: uuid.UUID, case_file: CaseFile) -> frozenset[str]:
    """Permissions of ``user_id`` on ``case_file``; empty when the user is not a member."""
    if case_file.created_by == user_id:
        return frozenset(permission.value for permission in CasePermission)
    member = db.get(CaseMember, (case_file.id, user_id))
    return frozenset(member.permissions) if member is not None else frozenset()


def can_manage_members(user: User, case_file: CaseFile) -> bool:
    """Only an ``admin`` or the creator of the case file may add members."""
    return user.role == UserRole.ADMIN or case_file.created_by == user.id


def record_access_denied(
    session_factory: sessionmaker[Session],
    *,
    case_file_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: str,
    method: str,
    route: str,
    permission: str,
    status: int,
    client_ip: str | None,
) -> bool:
    """Append and commit an ``access.denied`` event in a session of its own.

    The denied request's transaction is rolled back, so the event cannot ride on it. The
    caller must not hold a lock on the case file row (roll its own session back first),
    otherwise this write would wait for it forever. Returns ``False`` and logs when the
    event cannot be written; the denial stands either way.
    """
    try:
        with session_factory() as audit_db:
            append_event(
                audit_db,
                case_file_id=case_file_id,
                kind=EventKind.ACCESS_DENIED,
                actor_id=actor_id,
                actor_role=actor_role,
                after={
                    "method": method,
                    "route": route,
                    "permission": permission,
                    "status": status,
                },
                client_ip=client_ip,
            )
            audit_db.commit()
    except Exception:
        logger.exception(
            "Could not record access.denied for user %s on case file %s", actor_id, case_file_id
        )
        return False
    logger.warning(
        "Access denied: user %s lacks %s on case file %s (%s %s)",
        actor_id,
        permission,
        case_file_id,
        method,
        route,
    )
    return True


def add_member(
    db: Session,
    case_file: CaseFile,
    user_id: uuid.UUID,
    permissions: Iterable[str],
    actor: User,
) -> CaseMember:
    """Add ``user_id`` to ``case_file`` and append ``member.added`` in the caller's transaction.

    Locks the case file row first, so concurrent additions are serialized. Raises
    :class:`UnknownUserError` or :class:`MemberExistsError`; the caller commits.
    """
    lock_case_file(db, case_file.id)
    if db.get(User, user_id) is None:
        raise UnknownUserError(f"user {user_id} does not exist")
    if case_file.created_by == user_id or db.get(CaseMember, (case_file.id, user_id)):
        raise MemberExistsError(f"user {user_id} is already a member of {case_file.id}")
    member = CaseMember(
        case_file_id=case_file.id, user_id=user_id, permissions=normalize_permissions(permissions)
    )
    db.add(member)
    db.flush()
    append_event(
        db,
        case_file_id=case_file.id,
        kind=EventKind.MEMBER_ADDED,
        actor_id=actor.id,
        actor_role=actor.role.value,
        after={"user_id": str(user_id), "permissions": list(member.permissions)},
    )
    return member
