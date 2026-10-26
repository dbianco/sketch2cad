"""FastAPI application factory."""

from fastapi import FastAPI

from sketch2cad.api.errors import register_error_handlers
from sketch2cad.api.routers import (
    auth,
    calibration,
    cases,
    catalog,
    entities,
    events,
    evidence,
    health,
    jobs,
    plans,
    sources,
)
from sketch2cad.logging import configure_logging
from sketch2cad.package.signing import require_signing_key


def create_app() -> FastAPI:
    """Build the application; settings and the database are resolved lazily per request."""
    configure_logging()
    app = FastAPI(title="Sketch2CAD API", version="0.1.0")
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(cases.router)
    app.include_router(sources.router)
    app.include_router(jobs.router)
    app.include_router(events.router)
    app.include_router(catalog.router)
    app.include_router(plans.router)
    app.include_router(calibration.router)
    app.include_router(entities.router)
    app.include_router(evidence.router)
    return app


def create_server_app() -> FastAPI:
    """Entry point of the served API (``uvicorn --factory sketch2cad.api:create_server_app``).

    Refuses to start (``SystemExit`` naming the path, exit status 1) when the signing key
    at ``SIGNING_KEY_PATH`` is missing or its mode is wider than 0600 (jotdown H4-11).
    Tests build the app with :func:`create_app`, which skips this check.
    """
    require_signing_key()
    return create_app()
