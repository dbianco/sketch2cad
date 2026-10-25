"""FastAPI application factory."""

from fastapi import FastAPI

from sketch2cad.api.errors import register_error_handlers
from sketch2cad.api.routers import (
    auth,
    calibration,
    cases,
    catalog,
    events,
    health,
    jobs,
    plans,
    sources,
)
from sketch2cad.logging import configure_logging


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
    return app
