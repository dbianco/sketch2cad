"""FastAPI application factory."""

from fastapi import FastAPI

from sketch2cad.api.errors import register_error_handlers
from sketch2cad.api.routers import auth, health


def create_app() -> FastAPI:
    """Build the application; settings and the database are resolved lazily per request."""
    app = FastAPI(title="Sketch2CAD API", version="0.1.0")
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    return app
