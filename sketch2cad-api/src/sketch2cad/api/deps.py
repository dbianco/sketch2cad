"""Shared FastAPI dependencies: settings, the user, CSRF, case permissions and storage."""

import logging
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api.errors import ProblemError
from sketch2cad.auth.csrf import CSRF_HEADER, csrf_tokens_match, requires_csrf_check
from sketch2cad.auth.sessions import find_active_session
from sketch2cad.cases.permissions import (
    MANAGE_MEMBERS,
    CasePermission,
    can_manage_members,
    case_permissions,
    find_case,
    record_access_denied,
)
from sketch2cad.config import Settings, get_settings
from sketch2cad.db import session as db_session
from sketch2cad.db.models import (
    CaseFile,
    Entity,
    Evidence,
    Plan,
    Revision,
    Source,
    User,
    UserRole,
    UserSession,
)
from sketch2cad.db.session import get_session
from sketch2cad.storage import FilesystemStorage, Storage

logger = logging.getLogger(__name__)

SESSION_COOKIE = "s2c_session"
CSRF_COOKIE = "s2c_csrf"


def get_app_settings() -> Settings:
    """Resolve the settings per request so ``create_app()`` never needs them."""
    return get_settings()


def _unauthorized() -> ProblemError:
    return ProblemError(401, "Unauthorized", "Authentication is required.")


def get_current_session(
    db: Annotated[Session, Depends(get_session)],
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> UserSession:
    """Return the active session of the request's cookie owned by an active user, else 401."""
    if not token:
        raise _unauthorized()
    record = find_active_session(db, token)
    if record is None:
        raise _unauthorized()
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return record


def get_current_user(
    db: Annotated[Session, Depends(get_session)],
    record: Annotated[UserSession, Depends(get_current_session)],
) -> User:
    """Return the authenticated user; 401 problem+json when there is no valid session."""
    user = db.get(User, record.user_id)
    if user is None:
        raise _unauthorized()
    return user


def require_csrf(
    request: Request,
    record: Annotated[UserSession, Depends(get_current_session)],
    header: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
) -> None:
    """Reject a mutation whose ``X-CSRF-Token`` differs from the cookie or the session token.

    Safe methods (GET, HEAD, OPTIONS) are never checked. Requires a valid session first, so a
    request without one still gets 401. The 403 never echoes any token.
    """
    if not requires_csrf_check(request.method):
        return
    if not csrf_tokens_match(header, cookie, record.csrf_token):
        logger.warning(
            "CSRF check failed on %s %s for session %s",
            request.method,
            request.url.path,
            record.id,
        )
        raise ProblemError(403, "Forbidden", "A valid CSRF token is required.")


def get_storage(settings: Annotated[Settings, Depends(get_app_settings)]) -> Storage:
    """Filesystem object storage rooted at ``STORAGE_ROOT``."""
    return FilesystemStorage(settings.STORAGE_ROOT)


def get_audit_session_factory() -> sessionmaker[Session]:
    """Session factory for events that must outlive the request transaction."""
    return db_session.get_session_factory()


def case_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The case file does not exist.")


def _forbidden() -> ProblemError:
    return ProblemError(403, "Forbidden", "You do not have permission for this case file.")


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def _deny(
    request: Request,
    db: Session,
    audit_factory: sessionmaker[Session],
    user: User,
    case_file: CaseFile,
    permission: str,
) -> ProblemError:
    """Record ``access.denied`` and return the 403 to raise.

    Members lacking ``permission`` and non-members alike get 403 (H1-5, H6-3); only a
    missing or malformed id gives 404. The request session is rolled back first: it
    releases any row lock the event writer would wait on, and nothing of a denied request
    may be committed.
    """
    error = _forbidden()
    case_file_id, actor_id, actor_role = case_file.id, user.id, user.role.value
    db.rollback()
    record_access_denied(
        audit_factory,
        case_file_id=case_file_id,
        actor_id=actor_id,
        actor_role=actor_role,
        method=request.method,
        route=_route_template(request),
        permission=permission,
        status=error.status,
        client_ip=request.client.host if request.client else None,
    )
    return error


ADMIN_PERMISSION = "role:admin"
"""``permission`` of the ``access.denied`` event of an admin-only route."""


def require_admin(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """The current user if they are ``admin``; else 403 and ``access.denied`` (global chain)."""
    if user.role is UserRole.ADMIN:
        return user
    error = ProblemError(403, "Forbidden", "Only an admin may do this.")
    actor_id, actor_role = user.id, user.role.value
    db.rollback()
    record_access_denied(
        audit_factory,
        case_file_id=None,
        actor_id=actor_id,
        actor_role=actor_role,
        method=request.method,
        route=_route_template(request),
        permission=ADMIN_PERMISSION,
        status=error.status,
        client_ip=request.client.host if request.client else None,
    )
    raise error


def source_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The source does not exist.")


def _parse_uuid(value: str, not_found: Callable[[], ProblemError]) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise not_found() from None


def _existing_case(db: Session, case_id: str) -> CaseFile:
    case_file = find_case(db, _parse_uuid(case_id, case_not_found))
    if case_file is None:
        raise case_not_found()
    return case_file


def require_case_permission(permission: str) -> Callable[..., CaseFile]:
    """Dependency factory: the ``{case_id}`` case file if the user holds ``permission``.

    Missing or malformed ids give 404 without an event (there is no chain to write to).
    Every other denial, member or not, is a 403 that writes one ``access.denied`` event
    committed on its own.
    """
    required = CasePermission(permission).value

    def dependency(
        case_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> CaseFile:
        case_file = _existing_case(db, case_id)
        if required not in case_permissions(db, user.id, case_file):
            raise _deny(request, db, audit_factory, user, case_file, required)
        return case_file

    return dependency


def require_case_manager(
    case_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
) -> CaseFile:
    """The ``{case_id}`` case file if the user is ``admin`` or its creator, else denied."""
    case_file = _existing_case(db, case_id)
    if not can_manage_members(user, case_file):
        raise _deny(request, db, audit_factory, user, case_file, MANAGE_MEMBERS)
    return case_file


def require_source_permission(permission: str) -> Callable[..., Source]:
    """Dependency factory: the ``{source_id}`` source if the user holds ``permission``.

    The permission is checked on the source's case file. A missing or malformed source id
    gives 404 without an event; a denial is a 403 with ``access.denied`` on that case file.
    """
    required = CasePermission(permission).value

    def dependency(
        source_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> Source:
        source = db.get(Source, _parse_uuid(source_id, source_not_found))
        if source is None:
            raise source_not_found()
        case_file = db.get(CaseFile, source.case_file_id)
        if case_file is None:  # pragma: no cover - guaranteed by the foreign key
            raise source_not_found()
        if required not in case_permissions(db, user.id, case_file):
            raise _deny(request, db, audit_factory, user, case_file, required)
        return source

    return dependency


def plan_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The plan does not exist.")


def require_plan_permission(permission: str) -> Callable[..., Plan]:
    """Dependency factory: the ``{plan_id}`` plan if the user holds ``permission``.

    The permission is checked on the plan's case file. A missing or malformed plan id
    gives 404 without an event; a denial is a 403 with ``access.denied`` on that case file.
    """
    required = CasePermission(permission).value

    def dependency(
        plan_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> Plan:
        plan = db.get(Plan, _parse_uuid(plan_id, plan_not_found))
        if plan is None:
            raise plan_not_found()
        case_file = db.get(CaseFile, plan.case_file_id)
        if case_file is None:  # pragma: no cover - guaranteed by the foreign key
            raise plan_not_found()
        if required not in case_permissions(db, user.id, case_file):
            raise _deny(request, db, audit_factory, user, case_file, required)
        return plan

    return dependency


def revision_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The revision does not exist.")


def entity_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The entity does not exist.")


def _ensure_plan_permission(
    request: Request,
    db: Session,
    audit_factory: sessionmaker[Session],
    user: User,
    plan_id: uuid.UUID,
    required: str,
    not_found: Callable[[], ProblemError],
) -> None:
    """Check ``required`` on the case file of plan ``plan_id``; 403 with ``access.denied``."""
    plan = db.get(Plan, plan_id)
    case_file = None if plan is None else db.get(CaseFile, plan.case_file_id)
    if case_file is None:  # pragma: no cover - guaranteed by the foreign keys
        raise not_found()
    if required not in case_permissions(db, user.id, case_file):
        raise _deny(request, db, audit_factory, user, case_file, required)


def require_revision_permission(permission: str) -> Callable[..., Revision]:
    """Dependency factory: the ``{revision_id}`` revision if the user holds ``permission``.

    The permission is checked on the case file of the revision's plan. A missing or
    malformed id gives 404 without an event; a denial is a 403 with ``access.denied``.
    """
    required = CasePermission(permission).value

    def dependency(
        revision_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> Revision:
        revision = db.get(Revision, _parse_uuid(revision_id, revision_not_found))
        if revision is None:
            raise revision_not_found()
        _ensure_plan_permission(
            request, db, audit_factory, user, revision.plan_id, required, revision_not_found
        )
        return revision

    return dependency


def require_entity_permission(permission: str) -> Callable[..., Entity]:
    """Dependency factory: the ``{entity_id}`` entity if the user holds ``permission``.

    The case file is resolved through entity, revision and plan. A missing or malformed id
    gives 404 without an event; a denial is a 403 with ``access.denied``. Soft-deleted
    entities are returned too; the caller decides what they allow.
    """
    required = CasePermission(permission).value

    def dependency(
        entity_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> Entity:
        entity = db.get(Entity, _parse_uuid(entity_id, entity_not_found))
        if entity is None:
            raise entity_not_found()
        revision = db.get(Revision, entity.revision_id)
        if revision is None:  # pragma: no cover - guaranteed by the foreign key
            raise entity_not_found()
        _ensure_plan_permission(
            request, db, audit_factory, user, revision.plan_id, required, entity_not_found
        )
        return entity

    return dependency


def evidence_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The evidence does not exist.")


def require_evidence_permission(permission: str) -> Callable[..., Evidence]:
    """Dependency factory: the ``{evidence_id}`` evidence if the user holds ``permission``.

    The permission is checked on the evidence's case file. A missing or malformed id gives
    404 without an event; a denial is a 403 with ``access.denied`` on that case file.
    """
    required = CasePermission(permission).value

    def dependency(
        evidence_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    ) -> Evidence:
        evidence = db.get(Evidence, _parse_uuid(evidence_id, evidence_not_found))
        if evidence is None:
            raise evidence_not_found()
        case_file = db.get(CaseFile, evidence.case_file_id)
        if case_file is None:  # pragma: no cover - guaranteed by the foreign key
            raise evidence_not_found()
        if required not in case_permissions(db, user.id, case_file):
            raise _deny(request, db, audit_factory, user, case_file, required)
        return evidence

    return dependency
