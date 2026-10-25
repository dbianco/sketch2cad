"""Shared FastAPI dependencies: settings, the authenticated user, CSRF and case permissions."""

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
    is_member,
    record_access_denied,
)
from sketch2cad.config import Settings, get_settings
from sketch2cad.db import session as db_session
from sketch2cad.db.models import CaseFile, User, UserSession
from sketch2cad.db.session import get_session

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
    """Record ``access.denied`` and return the error to raise.

    A non-member gets the same 404 as for a missing case file, so the response does not
    reveal that it exists; a member lacking ``permission`` gets 403. The request session is
    rolled back first: it releases any row lock the event writer would wait on, and nothing
    of a denied request may be committed.
    """
    error = _forbidden() if is_member(db, user.id, case_file) else case_not_found()
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


def _existing_case(db: Session, case_id: str) -> CaseFile:
    try:
        parsed = uuid.UUID(case_id)
    except ValueError:
        raise case_not_found() from None
    case_file = find_case(db, parsed)
    if case_file is None:
        raise case_not_found()
    return case_file


def require_case_permission(permission: str) -> Callable[..., CaseFile]:
    """Dependency factory: the ``{case_id}`` case file if the user holds ``permission``.

    Missing or malformed ids give 404 without an event (there is no chain to write to).
    Every other denial writes one ``access.denied`` event committed on its own.
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
