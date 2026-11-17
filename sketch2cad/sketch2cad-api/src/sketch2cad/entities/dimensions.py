"""Declared dimensions and their discrepancy flag (spec sections 8.2, 11.3, 11.4; REQ-06).

A ``dimension`` entity carries ``declared_value_m``, the value written on the sketch or typed
by the technician (the metric truth, user data), and ``measured_value_m``, always derived
from ``p1_m`` and ``p2_m`` (:func:`measured_length`). The server never edits either value
to make them agree: when they differ by more than ``dimension.discrepancy_tolerance_m``
the entity is flagged with ``discrepancy`` (:func:`has_discrepancy`). The flag is computed
on read, never stored, so a new tolerance applies to existing entities at once.

A declared dimension does not change any ``metric_origin`` (spec section 11.3 gives the
tool no origin): ``L_DIMENSIONS`` is not a metric layer. :func:`discrepant_dimensions`
lists the flagged dimensions of a revision for the H3 metric status extension (task 076).

:class:`DimensionTolerances` defaults to the spec value, so worker code can use it without
the API-only settings; :meth:`DimensionTolerances.resolve` applies the per-incident-type
and global overrides of :mod:`sketch2cad.config_params` over the environment default.
"""

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from sketch2cad.config import Settings
from sketch2cad.config_params import case_incident_type, code_default, get_param
from sketch2cad.db.models import Entity, IncidentType
from sketch2cad.geometry.metrics import TANGENCY_TOLERANCE_M

DIMENSION_TYPE = "dimension"
TOLERANCE_SETTING = "DIMENSION_DISCREPANCY_TOLERANCE_M"
TOLERANCE_PARAM = "dimension.discrepancy_tolerance_m"
ROUNDING_MARGIN_M = TANGENCY_TOLERANCE_M
"""Float noise absorbed at the tolerance boundary (the repo's 1e-9 m geometric epsilon)."""


@dataclass(frozen=True)
class DimensionTolerances:
    """Tolerance of spec section 11.4; the default is the registry code default."""

    discrepancy_tolerance_m: float = code_default(TOLERANCE_PARAM)

    def __post_init__(self) -> None:
        if not self.discrepancy_tolerance_m > 0:
            raise ValueError("discrepancy_tolerance_m must be greater than 0")

    @classmethod
    def from_settings(cls, settings: Settings) -> "DimensionTolerances":
        """Read the tolerance from ``DIMENSION_DISCREPANCY_TOLERANCE_M`` of the API settings."""
        return cls(discrepancy_tolerance_m=settings.DIMENSION_DISCREPANCY_TOLERANCE_M)

    @classmethod
    def from_environment(cls) -> "DimensionTolerances":
        """Read only ``DIMENSION_DISCREPANCY_TOLERANCE_M`` (default when unset).

        Raises ``pydantic.ValidationError`` when the variable breaks the settings constraint.
        """
        return cls.resolve(None, None)

    @classmethod
    def resolve(
        cls, session: Session | None, incident_type: IncidentType | None
    ) -> "DimensionTolerances":
        """Resolve ``dimension.discrepancy_tolerance_m`` (overrides, then the default)."""
        value = get_param(TOLERANCE_PARAM, incident_type=incident_type, session=session)
        return cls(discrepancy_tolerance_m=float(value))

    @classmethod
    def for_case(cls, session: Session, case_file_id: uuid.UUID) -> "DimensionTolerances":
        """Tolerance for the incident type of the case file ``case_file_id``."""
        return cls.resolve(session, case_incident_type(session, case_file_id))


def measured_length(p1: Iterable[float], p2: Iterable[float]) -> float:
    """Distance in metres between the two dimension points ``p1_m`` and ``p2_m``."""
    (x1, y1), (x2, y2) = tuple(p1), tuple(p2)
    return math.hypot(x2 - x1, y2 - y1)


def has_discrepancy(declared: float | None, measured: float, tolerance: float) -> bool:
    """Whether ``|declared - measured|`` is strictly greater than ``tolerance``.

    A plain measurement (``declared`` is ``None``) never has a discrepancy. A difference
    equal to the tolerance is not a discrepancy, up to :data:`ROUNDING_MARGIN_M` of float
    noise (``|4.22 - 4.20|`` is not exactly ``0.02`` in binary).
    """
    if declared is None:
        return False
    return abs(declared - measured) - tolerance > ROUNDING_MARGIN_M


def dimension_discrepancy(
    entity_type: str, geometry: Mapping[str, Any], tolerances: DimensionTolerances
) -> bool | None:
    """``discrepancy`` of an entity: a bool for a dimension, ``None`` for other types.

    ``measured`` is recomputed from ``p1_m`` and ``p2_m``, never read from the stored value.
    """
    if entity_type != DIMENSION_TYPE:
        return None
    measured = measured_length(geometry["p1_m"], geometry["p2_m"])
    declared = geometry.get("declared_value_m")
    return has_discrepancy(declared, measured, tolerances.discrepancy_tolerance_m)


def discrepant_dimensions(
    entities: Iterable[Entity], tolerances: DimensionTolerances
) -> list[Entity]:
    """Live dimensions of ``entities`` whose declared and measured values disagree, in order."""
    return [
        entity
        for entity in entities
        if entity.deleted_at is None
        and dimension_discrepancy(entity.type, entity.geometry, tolerances) is True
    ]
