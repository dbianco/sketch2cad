"""Login, logout and current-user endpoints (cookie-based sessions)."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    get_app_settings,
    get_current_session,
    get_current_user,
    require_csrf,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.auth.lockout import Clock, get_clock, is_locked, register_failure, reset_failures
from sketch2cad.auth.passwords import (
    hash_password,
    needs_rehash,
    verify_dummy_password,
    verify_password,
)
from sketch2cad.auth.sessions import create_session, revoke_session
from sketch2cad.config import Settings
from sketch2cad.db.models import User, UserRole, UserSession
from sketch2cad.db.session import get_session

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["auth"])

INSECURE_COOKIE_ENV = "dev"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None
    role: UserRole


class UserEnvelope(BaseModel):
    user: UserOut


def _envelope(user: User) -> UserEnvelope:
    return UserEnvelope(
        user=UserOut(
            id=user.id, username=user.username, display_name=user.display_name, role=user.role
        )
    )


def _invalid_credentials() -> ProblemError:
    return ProblemError(401, "Unauthorized", "Invalid username or password.")


def _account_locked(locked_until: datetime) -> ProblemError:
    return ProblemError(
        423,
        "Locked",
        "The account is temporarily locked after too many failed login attempts.",
        extensions={"locked_until": locked_until.astimezone(UTC).isoformat()},
    )


def _secure_cookies(settings: Settings) -> bool:
    return settings.APP_ENV != INSECURE_COOKIE_ENV


@router.post("/auth/login")
def login(
    body: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> UserEnvelope:
    """Check the credentials and open a session; failures never reveal whether a user exists.

    A locked account gets 423 even with the right password; the password is still verified
    first so the response time does not reveal whether it was correct.
    """
    now = clock()
    user = db.scalars(select(User).where(User.username == body.username)).one_or_none()
    if user is None:
        verify_dummy_password(body.password)
        raise _invalid_credentials()
    password_ok = verify_password(user.password_hash, body.password)
    if not user.is_active:
        raise _invalid_credentials()
    if user.locked_until is not None and is_locked(user, now):
        logger.info("Login rejected for locked user %s", user.id)
        raise _account_locked(user.locked_until)
    if not password_ok:
        result = register_failure(db, user.id, now, settings.AUTH_LOCKOUT_MINUTES)
        db.commit()
        if result.locked_until is not None:
            logger.warning("User %s locked after %d failed logins", user.id, result.failed_attempts)
        raise _invalid_credentials()
    reset_failures(user)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    issued = create_session(db, user, settings.AUTH_SESSION_TTL_MINUTES, now=now)
    db.commit()
    logger.info("User %s signed in", user.id)

    secure = _secure_cookies(settings)
    max_age = settings.AUTH_SESSION_TTL_MINUTES * 60
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.record.csrf_token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )
    return _envelope(user)


@router.post("/auth/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_session)],
    record: Annotated[UserSession, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    """Revoke the current session and clear both authentication cookies."""
    revoke_session(db, record)
    db.commit()
    logger.info("User %s signed out", record.user_id)

    secure = _secure_cookies(settings)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax")


@router.get("/auth/me")
def me(user: Annotated[User, Depends(get_current_user)]) -> UserEnvelope:
    """Return the authenticated user."""
    return _envelope(user)
