"""RFC 9457 problem details (``application/problem+json``) and exception handlers."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
DEFAULT_PROBLEM_TYPE = "about:blank"


class ProblemError(Exception):
    """An error rendered as a problem details response."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        type: str = DEFAULT_PROBLEM_TYPE,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.headers = headers
        self.extensions = dict(extensions or {})


def problem_response(
    status: int,
    title: str,
    detail: str,
    type: str = DEFAULT_PROBLEM_TYPE,
    headers: Mapping[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    """Build a ``application/problem+json`` response with optional extension members."""
    body: dict[str, Any] = {"type": type, "title": title, "status": status, "detail": detail}
    body.update(extensions)
    return JSONResponse(
        content=jsonable_encoder(body),
        status_code=status,
        headers=headers,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def _status_title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


async def _handle_problem(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ProblemError):
        raise exc
    return problem_response(
        exc.status, exc.title, exc.detail, exc.type, exc.headers, **exc.extensions
    )


async def _handle_validation(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    errors = [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return problem_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        _status_title(HTTPStatus.UNPROCESSABLE_ENTITY),
        "The request failed validation.",
        errors=errors,
    )


async def _handle_http(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    title = _status_title(exc.status_code)
    detail = exc.detail or title
    return problem_response(exc.status_code, title, detail, headers=exc.headers)


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s (%s)",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return problem_response(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        _status_title(HTTPStatus.INTERNAL_SERVER_ERROR),
        "An unexpected error occurred.",
    )


def register_error_handlers(app: FastAPI) -> None:
    """Render every error of ``app`` as problem details."""
    app.add_exception_handler(ProblemError, _handle_problem)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(Exception, _handle_unexpected)
