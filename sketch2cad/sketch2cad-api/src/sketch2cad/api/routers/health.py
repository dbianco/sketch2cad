"""Liveness and database readiness check."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sketch2cad.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    """Return 200 when the database answers ``SELECT 1`` and 503 otherwise."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Health check failed: database unavailable (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "error"})
    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})
