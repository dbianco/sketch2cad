"""Shared FastAPI dependencies: settings, the authenticated user and the CSRF check."""

import logging
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.orm import Session

from sketch2cad.api.errors import ProblemError
from sketch2cad.auth.csrf import CSRF_HEADER, csrf_tokens_match, requires_csrf_check
from sketch2cad.auth.sessions import find_active_session
from sketch2cad.config import Settings, get_settings
from sketch2cad.db.models import User, UserSession
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
