"""API routers; everything except the health check lives under ``/api/v1``."""

from enum import Enum

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"


def api_v1_router(tags: list[str | Enum] | None = None) -> APIRouter:
    """Return a router mounted under the versioned ``/api/v1`` prefix."""
    return APIRouter(prefix=API_V1_PREFIX, tags=tags)
