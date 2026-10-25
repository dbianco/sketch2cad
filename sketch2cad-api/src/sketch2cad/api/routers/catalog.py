"""Active catalog endpoint: the layers and symbols the editor offers (REQ-09).

The catalog is global, not tied to a case file, so any authenticated user may read it.
"""

import uuid
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.api.deps import get_current_user
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.catalog import CatalogDefinition
from sketch2cad.db.models import Catalog, User
from sketch2cad.db.session import get_session

router = api_v1_router(tags=["catalog"])

CATALOG_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication is required."},
    404: {"description": "No catalog is active."},
}


class CatalogOut(CatalogDefinition):
    """The active catalog version with its layers and symbols (SVG inline)."""

    id: uuid.UUID


@router.get("/catalog/active", responses=CATALOG_RESPONSES)
def get_active_catalog(
    db: Annotated[Session, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> CatalogOut:
    """Return the active catalog; requires a session but no case permission."""
    catalog = db.scalars(select(Catalog).where(Catalog.is_active)).one_or_none()
    if catalog is None:
        raise ProblemError(404, "Not Found", "No catalog is active.")
    return CatalogOut.model_validate(
        {
            "id": catalog.id,
            "version": catalog.version,
            "layers": catalog.layers,
            "symbols": catalog.symbols,
        }
    )
