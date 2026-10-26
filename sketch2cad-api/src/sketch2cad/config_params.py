"""Layered configuration parameters (REQ-19; spec sections 10, 11.4 and 19).

:data:`PARAMETERS` registers every parameter the institution may set. A parameter resolves
in this order:

1. the override for the incident type of the case (``config_override``, scope
   ``incident_type``), when the parameter allows it;
2. the global override (scope ``global``);
3. the environment/code default: the :class:`~sketch2cad.config.Settings` field named
   :attr:`ParamSpec.env_var`, read on its own (:func:`env_default`) so the worker never
   needs the API-only settings. The field holds the code default and the constraints, so
   there is a single source of truth for both.

:func:`get_param` / :func:`get_params` without a session return the default layer only
(pure and worker contexts). Overrides are written only through :func:`set_override` and
:func:`delete_override`, each appending one ``config.changed`` event to the global chain.
"""

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any, cast

from annotated_types import Ge, Gt, Le, Lt
from pydantic import AllowInfNan, Strict, TypeAdapter, ValidationError, create_model
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event, lock_chain
from sketch2cad.config import Settings
from sketch2cad.db.models import CaseFile, ConfigOverride, ConfigScope, IncidentType, User

logger = logging.getLogger(__name__)

ParamValue = bool | int | float | str
"""JSON scalar a parameter takes."""

BOTH_SCOPES = frozenset({ConfigScope.GLOBAL, ConfigScope.INCIDENT_TYPE})
GLOBAL_ONLY = frozenset({ConfigScope.GLOBAL})
ENVIRONMENT_ONLY: frozenset[ConfigScope] = frozenset()

_TYPE_NAMES: dict[type, str] = {bool: "bool", int: "int", float: "float", str: "str"}
_BOUNDS = ((Gt, "gt"), (Ge, "ge"), (Lt, "lt"), (Le, "le"))


class ConfigValueError(ValueError):
    """An override that does not fit the registry: wrong type, bound, scope or key (422)."""


@dataclass(frozen=True)
class ParamSpec:
    """A registered parameter; its settings field gives type, default, bounds and description."""

    name: str
    env_var: str
    section: str
    scopes: frozenset[ConfigScope]

    @property
    def field(self) -> FieldInfo:
        return Settings.model_fields[self.env_var]

    @property
    def description(self) -> str:
        """The settings field docstring, on one line."""
        return " ".join((self.field.description or "").split())

    @property
    def type(self) -> type:
        return cast(type, self.field.annotation)

    @property
    def type_name(self) -> str:
        return _TYPE_NAMES[self.type]

    @property
    def code_default(self) -> ParamValue:
        """Default when neither the environment nor an override sets the parameter."""
        return cast(ParamValue, self.field.default)

    @property
    def incident_type_overridable(self) -> bool:
        return ConfigScope.INCIDENT_TYPE in self.scopes

    def constraints(self) -> dict[str, float]:
        """Numeric bounds of the field, e.g. ``{"gt": 0}``."""
        bounds: dict[str, float] = {}
        for item in self.field.metadata:
            for kind, label in _BOUNDS:
                if isinstance(item, kind):
                    bounds[label] = float(getattr(item, label))
        return bounds

    def validate(self, value: object) -> ParamValue:
        """``value`` as the parameter type; raises :class:`ConfigValueError` when it does not fit.

        JSON types are strict (``"1.5"`` is not a float, ``1`` is not a bool); an integer is
        accepted for a float parameter.
        """
        try:
            return _adapter(self.name).validate_python(value)
        except ValidationError as exc:
            reasons = "; ".join(error["msg"] for error in exc.errors())
            raise ConfigValueError(f"Invalid value for {self.name}: {reasons}.") from None


_REGISTRY: tuple[tuple[str, str, frozenset[ConfigScope]], ...] = (
    ("qc.min_short_side_amber", "10", BOTH_SCOPES),
    ("qc.min_short_side_red", "10", BOTH_SCOPES),
    ("qc.min_laplacian_var_amber", "10", BOTH_SCOPES),
    ("qc.min_laplacian_var_red", "10", BOTH_SCOPES),
    ("qc.min_contrast_amber", "10", BOTH_SCOPES),
    ("calibration.max_reprojection_error_mm", "11.4", BOTH_SCOPES),
    ("calibration.max_scale_discrepancy_pct", "11.4", BOTH_SCOPES),
    ("dimension.discrepancy_tolerance_m", "11.4", BOTH_SCOPES),
    ("export.control_distance_tolerance_pct", "11.4", BOTH_SCOPES),
    ("suggestions.min_confidence_highlight", "11.4", BOTH_SCOPES),
    ("suggest_lines.threshold_block_size", "10", BOTH_SCOPES),
    ("suggest_lines.threshold_c", "10", BOTH_SCOPES),
    ("suggest_lines.opening_kernel_px", "10", BOTH_SCOPES),
    ("suggest_lines.simplify_tolerance_mm", "10", BOTH_SCOPES),
    ("suggest_lines.min_length_mm", "10", BOTH_SCOPES),
    ("suggest_lines.merge_angle_deg", "10", BOTH_SCOPES),
    ("ocr.min_confidence", "10", BOTH_SCOPES),
    ("ai_assist.enabled", "19", GLOBAL_ONLY),
    ("legal.disclaimer_enabled", "13.2", GLOBAL_ONLY),
)
"""Name, spec section and allowed override scopes of every parameter."""

PARAMETERS: Mapping[str, ParamSpec] = {
    name: ParamSpec(name, name.upper().replace(".", "_"), section, scopes)
    for name, section, scopes in _REGISTRY
}
"""The registry; ``env_var`` is the name in upper case with ``_`` for ``.``."""


AI_ASSIST_PARAM = "ai_assist.enabled"
"""Global switch of the AI assistance (spec section 19); not a Split flag."""


def param_spec(name: str) -> ParamSpec:
    """The registered parameter ``name``; raises ``KeyError`` naming it when it is unknown."""
    try:
        return PARAMETERS[name]
    except KeyError:
        raise KeyError(f"Unknown configuration parameter {name!r}.") from None


def code_default(name: str) -> Any:
    """Code default of ``name`` (ignores the environment); typed ``Any`` for dataclass fields."""
    return param_spec(name).code_default


@cache
def _adapter(name: str) -> TypeAdapter[ParamValue]:
    spec = param_spec(name)
    extra: list[Any] = [Strict()]
    if spec.type is float:
        extra.append(AllowInfNan(False))
    annotated: Any = Annotated[spec.type, *extra, *spec.field.metadata]
    return TypeAdapter(annotated)


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")


@cache
def _environment_model(env_var: str) -> type[BaseSettings]:
    field = Settings.model_fields[env_var]
    fields: dict[str, Any] = {env_var: (field.annotation, field)}
    return create_model(f"_Env_{env_var}", __base__=_EnvironmentSettings, **fields)


def env_default(name: str) -> ParamValue:
    """Default layer of ``name``: its environment variable, else the code default.

    Reads only that variable (never the API-only settings). Raises
    ``pydantic.ValidationError`` when the variable breaks the field constraints.
    """
    env_var = param_spec(name).env_var
    return cast(ParamValue, getattr(_environment_model(env_var)(), env_var))


def _stored(spec: ParamSpec, row: ConfigOverride) -> ParamValue | None:
    """The stored value, or ``None`` (logged) when it no longer fits the registry."""
    try:
        return spec.validate(row.value)
    except ConfigValueError:
        logger.error("Ignoring invalid %s override %s of %s", row.scope, row.id, row.key)
        return None


def _overrides(
    session: Session, specs: list[ParamSpec], incident_type: IncidentType | None
) -> dict[tuple[str, str], ParamValue]:
    keys = [spec.name for spec in specs]
    scopes = [ConfigOverride.scope == ConfigScope.GLOBAL.value]
    if incident_type is not None:
        scopes.append(
            and_(
                ConfigOverride.scope == ConfigScope.INCIDENT_TYPE.value,
                ConfigOverride.incident_type == incident_type,
            )
        )
    rows = session.scalars(select(ConfigOverride).where(ConfigOverride.key.in_(keys), or_(*scopes)))
    found: dict[tuple[str, str], ParamValue] = {}
    for row in rows:
        value = _stored(PARAMETERS[row.key], row)
        if value is not None:
            found[(row.key, row.scope)] = value
    return found


def get_params(
    names: Iterable[str],
    *,
    incident_type: IncidentType | None = None,
    session: Session | None = None,
) -> dict[str, ParamValue]:
    """Resolve several parameters with one query (see :func:`get_param`)."""
    specs = [param_spec(name) for name in names]
    found = {} if session is None else _overrides(session, specs, incident_type)
    values: dict[str, ParamValue] = {}
    for spec in specs:
        layers: list[ConfigScope] = []
        if incident_type is not None and spec.incident_type_overridable:
            layers.append(ConfigScope.INCIDENT_TYPE)
        if ConfigScope.GLOBAL in spec.scopes:
            layers.append(ConfigScope.GLOBAL)
        keys = [(spec.name, scope.value) for scope in layers]
        chosen = next((found[key] for key in keys if key in found), None)
        values[spec.name] = env_default(spec.name) if chosen is None else chosen
    return values


def get_param(
    name: str, *, incident_type: IncidentType | None = None, session: Session | None = None
) -> ParamValue:
    """Value of ``name``: incident-type override, then global override, then the default.

    Without ``session`` only the environment/code default is returned. Raises ``KeyError``
    for an unknown parameter.
    """
    return get_params([name], incident_type=incident_type, session=session)[name]


def ai_assist_enabled(session: Session) -> bool:
    """``ai_assist.enabled``: the global override, else ``AI_ASSIST_ENABLED`` (true by default).

    The switch of the AI jobs (``suggest_lines``, ``ocr_crop``). Callers read it at every
    request, enqueue and run, so an admin change applies without a restart.
    """
    return bool(get_param(AI_ASSIST_PARAM, session=session))


def case_incident_type(session: Session, case_file_id: uuid.UUID) -> IncidentType | None:
    """Incident type of the case file, the key of its per-incident-type overrides."""
    return session.scalar(select(CaseFile.incident_type).where(CaseFile.id == case_file_id))


def list_overrides(session: Session) -> list[ConfigOverride]:
    """Every stored override, ordered by key, scope and incident type."""
    query = select(ConfigOverride).order_by(
        ConfigOverride.key, ConfigOverride.scope, ConfigOverride.incident_type
    )
    return list(session.scalars(query))


def _check_target(spec: ParamSpec, scope: ConfigScope, incident_type: IncidentType | None) -> None:
    if scope not in spec.scopes:
        raise ConfigValueError(f"{spec.name} cannot be overridden with scope {scope.value}.")
    if (scope is ConfigScope.INCIDENT_TYPE) != (incident_type is not None):
        raise ConfigValueError("incident_type is required for scope incident_type, else null.")


def _find(
    session: Session, name: str, scope: ConfigScope, incident_type: IncidentType | None
) -> ConfigOverride | None:
    query = select(ConfigOverride).where(
        ConfigOverride.key == name, ConfigOverride.scope == scope.value
    )
    if incident_type is not None:
        query = query.where(ConfigOverride.incident_type == incident_type)
    return session.scalars(query).one_or_none()


def _snapshot(row: ConfigOverride) -> dict[str, Any]:
    return {
        "key": row.key,
        "scope": row.scope,
        "incident_type": None if row.incident_type is None else row.incident_type.value,
        "value": row.value,
    }


def _known(name: str) -> ParamSpec:
    try:
        return param_spec(name)
    except KeyError as exc:
        raise ConfigValueError(str(exc.args[0])) from None


def set_override(
    session: Session,
    *,
    name: str,
    scope: ConfigScope,
    incident_type: IncidentType | None,
    value: object,
    actor: User,
    client_ip: str | None = None,
) -> ConfigOverride:
    """Create or replace an override and append one ``config.changed`` event (global chain).

    Takes the global chain lock first, which also serializes concurrent writes of the same
    override. Raises :class:`ConfigValueError` for an unknown parameter, a scope the
    parameter does not allow or a value that does not fit. The caller commits.
    """
    spec = _known(name)
    _check_target(spec, scope, incident_type)
    checked = spec.validate(value)
    lock_chain(session, None)
    row = _find(session, name, scope, incident_type)
    before = None if row is None else _snapshot(row)
    if row is None:
        row = ConfigOverride(key=name, scope=scope.value, incident_type=incident_type)
        session.add(row)
    row.value = checked
    row.updated_by = actor.id
    session.flush()
    _append(session, actor, before, _snapshot(row), client_ip)
    logger.info("Config override %s (%s %s) set by %s", name, scope, incident_type, actor.id)
    return row


def delete_override(
    session: Session,
    *,
    name: str,
    scope: ConfigScope,
    incident_type: IncidentType | None,
    actor: User,
    client_ip: str | None = None,
) -> bool:
    """Remove an override and append one ``config.changed`` event; ``False`` when absent.

    Raises :class:`ConfigValueError` like :func:`set_override`. The caller commits.
    """
    spec = _known(name)
    _check_target(spec, scope, incident_type)
    lock_chain(session, None)
    row = _find(session, name, scope, incident_type)
    if row is None:
        return False
    before = _snapshot(row)
    session.delete(row)
    session.flush()
    _append(session, actor, before, None, client_ip)
    logger.info(
        "Config override %s (%s %s) removed by %s", name, scope.value, incident_type, actor.id
    )
    return True


def _append(
    session: Session,
    actor: User,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    client_ip: str | None,
) -> None:
    append_event(
        session,
        case_file_id=None,
        kind=EventKind.CONFIG_CHANGED,
        actor_id=actor.id,
        actor_role=actor.role.value,
        before=before,
        after=after,
        client_ip=client_ip,
    )
