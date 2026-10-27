"""Configuration parameter endpoints (REQ-19; spec sections 3, 11.4 and 19).

Any authenticated user may read the registry with its defaults and overrides. Only an
``admin`` may set or remove an override; every change appends one ``config.changed``
event to the global chain, and a denied attempt one ``access.denied`` there.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel, StrictBool, StrictFloat, StrictInt, StrictStr
from sqlalchemy.orm import Session

from sketch2cad.api.deps import get_current_user, require_admin, require_csrf
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.config_params import (
    PARAMETERS,
    ConfigValueError,
    ParamSpec,
    ai_assist_enabled,
    delete_override,
    env_default,
    list_overrides,
    set_override,
)
from sketch2cad.db.models import ConfigOverride, ConfigScope, IncidentType, User
from sketch2cad.db.session import get_session

router = api_v1_router(tags=["config"])

ParamValueIn = StrictBool | StrictInt | StrictFloat | StrictStr
ParamValueOut = bool | int | float | str

WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication is required."},
    403: {"description": "Missing CSRF token, or the user is not an admin (access.denied)."},
    422: {
        "description": "Unknown parameter, a scope the parameter does not allow, an "
        "``incident_type`` missing for scope ``incident_type`` (or given for ``global``), "
        "or a value of the wrong type or outside the parameter bounds.",
    },
}


class OverrideOut(BaseModel):
    """A stored override; ``incident_type`` is null for scope ``global``."""

    scope: ConfigScope
    incident_type: IncidentType | None
    value: ParamValueOut
    updated_by: uuid.UUID
    updated_at: datetime


class ParamOut(BaseModel):
    """A parameter: ``default`` is the environment/code default; ``scopes`` the allowed
    override scopes (empty: environment only); ``constraints`` its numeric bounds
    (``gt``, ``ge``, ``lt``, ``le``)."""

    name: str
    type: Literal["bool", "int", "float", "str"]
    description: str
    section: str
    env_var: str
    default: ParamValueOut
    constraints: dict[str, float]
    scopes: list[ConfigScope]
    overrides: list[OverrideOut]


class ParamListOut(BaseModel):
    params: list[ParamOut]


class OverrideIn(BaseModel):
    """New override value; ``incident_type`` only (and required) for scope ``incident_type``."""

    value: ParamValueIn
    scope: ConfigScope
    incident_type: IncidentType | None = None


def _param_out(spec: ParamSpec, rows: list[ConfigOverride]) -> ParamOut:
    return ParamOut.model_validate(
        {
            "name": spec.name,
            "type": spec.type_name,
            "description": spec.description,
            "section": spec.section,
            "env_var": spec.env_var,
            "default": env_default(spec.name),
            "constraints": spec.constraints(),
            "scopes": sorted(spec.scopes),
            "overrides": [
                {
                    "scope": row.scope,
                    "incident_type": row.incident_type,
                    "value": row.value,
                    "updated_by": row.updated_by,
                    "updated_at": row.updated_at,
                }
                for row in rows
                if row.key == spec.name
            ],
        }
    )


def _one(db: Session, name: str) -> ParamOut:
    return _param_out(PARAMETERS[name], list_overrides(db))


def _invalid(db: Session, exc: ConfigValueError) -> ProblemError:
    db.rollback()
    return ProblemError(422, "Unprocessable Entity", str(exc))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/config/params", responses={401: {"description": "Authentication is required."}})
def get_config_params(
    db: Annotated[Session, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ParamListOut:
    """Every configuration parameter with its default and overrides; any signed-in user.

    Resolution order: override of the case's incident type, global override, default.
    """
    rows = list_overrides(db)
    return ParamListOut(params=[_param_out(spec, rows) for spec in PARAMETERS.values()])


class AiAssistFeatureOut(BaseModel):
    enabled: bool


class FeaturesOut(BaseModel):
    """Effective feature switches, for the client to hide controls of disabled features."""

    ai_assist: AiAssistFeatureOut


@router.get("/config/features", responses={401: {"description": "Authentication is required."}})
def get_config_features(
    db: Annotated[Session, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FeaturesOut:
    """Effective feature switches; any signed-in user.

    ``ai_assist.enabled`` is the global override, else ``AI_ASSIST_ENABLED`` (default
    true). When false the AI job kinds (``suggest_lines``, ``ocr_crop``) give 404 and no
    suggestions are queued, so the client should hide their controls.
    """
    return FeaturesOut(ai_assist=AiAssistFeatureOut(enabled=ai_assist_enabled(db)))


@router.put(
    "/config/params/{name}", dependencies=[Depends(require_csrf)], responses=WRITE_RESPONSES
)
def put_config_param(
    request: Request,
    name: str,
    body: OverrideIn,
    db: Annotated[Session, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> ParamOut:
    """Create or replace an override (admin); appends one ``config.changed`` event."""
    try:
        set_override(
            db,
            name=name,
            scope=body.scope,
            incident_type=body.incident_type,
            value=body.value,
            actor=admin,
            client_ip=_client_ip(request),
        )
    except ConfigValueError as exc:
        raise _invalid(db, exc) from None
    db.commit()
    return _one(db, name)


@router.delete(
    "/config/params/{name}",
    dependencies=[Depends(require_csrf)],
    responses={**WRITE_RESPONSES, 404: {"description": "There is no such override."}},
)
def delete_config_param(
    request: Request,
    name: str,
    scope: ConfigScope,
    db: Annotated[Session, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
    incident_type: Annotated[IncidentType | None, Query()] = None,
) -> ParamOut:
    """Remove an override (admin); appends one ``config.changed`` event."""
    try:
        removed = delete_override(
            db,
            name=name,
            scope=scope,
            incident_type=incident_type,
            actor=admin,
            client_ip=_client_ip(request),
        )
    except ConfigValueError as exc:
        raise _invalid(db, exc) from None
    if not removed:
        db.rollback()
        raise ProblemError(404, "Not Found", "There is no such override.")
    db.commit()
    return _one(db, name)
