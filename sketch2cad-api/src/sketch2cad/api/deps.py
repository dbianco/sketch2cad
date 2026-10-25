"""Shared FastAPI dependencies: settings and the authenticated user."""

from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from sketch2cad.api.errors import ProblemError
from sketch2cad.auth.sessions import find_active_session
from sketch2cad.config import Settings, get_settings
from sketch2cad.db.models import User, UserSession
from sketch2cad.db.session import get_session

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
