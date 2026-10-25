"""Metric status of a plan and provenance defaults of new entities (spec 11.3, REQ-07, US-11).

Pure functions with no database, FastAPI or I/O: callers pass the plan's ``source_mode``,
its active calibration (method and ``as_metric``) and lightweight entity inputs. Values
are the plain strings stored in the database, so ``StrEnum`` members of
``sketch2cad.db.models`` can be passed and compared directly.

Initial rule set (H2):

- A plan without an accepted calibration, or calibrated with method ``none``, is
  ``non_metric`` and lists no blocking entities: the calibration blocks it, not an entity.
- Otherwise every live entity in ``L_STRUCTURE`` or ``L_ACCESS``, or referencing an
  evidence item, whose ``metric_origin`` is not metric (``trace_only``, ``none``) blocks
  ``metric``; any blocking entity makes the plan ``approximate``.
- With no blocking entity the plan is ``metric`` when some relevant entity exists (all of
  them carry a metric origin), or, when there is none, only for a ``scaled_drawing`` whose
  calibration was accepted as metric: a ``schematic_sketch`` gets its metric truth from
  dimensions, so an empty one stays ``approximate``.

Hand-drawn entities get ``scaled_drawing`` in a ``scaled_drawing`` plan whose calibration
was accepted as metric, otherwise ``none`` (:func:`default_metric_origin`).

Extension point (H3): dimension-based origins (``declared_dimension``, ``triangulation``,
``baseline_offset``) are assigned by the H3 editor tools, not here; H3 extends
:func:`is_blocking` (for example, a polyline is ``declared_dimension`` only when every
segment has a declared length) and :func:`default_metric_origin` per ``entity_type``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

METRIC: Final = "metric"
APPROXIMATE: Final = "approximate"
NON_METRIC: Final = "non_metric"

SCALED_DRAWING_MODE: Final = "scaled_drawing"
NO_CALIBRATION_METHOD: Final = "none"

ORIGIN_SCALED_DRAWING: Final = "scaled_drawing"
ORIGIN_NONE: Final = "none"
METRIC_ORIGINS: Final = frozenset(
    {"declared_dimension", "triangulation", "baseline_offset", "scaled_drawing", "derived"}
)
"""``metric_origin`` values that count as metric (spec section 11.3)."""

METRIC_LAYERS: Final = frozenset({"L_STRUCTURE", "L_ACCESS"})
"""Layers whose entities must all be metric for the plan to be ``metric``."""


@dataclass(frozen=True)
class MetricEntity:
    """What the metric status needs to know about one entity."""

    id: str
    type: str
    layer: str
    metric_origin: str
    references_evidence: bool = False
    deleted: bool = False


@dataclass(frozen=True)
class MetricStatusResult:
    """Metric status of a plan and the ids of the entities that keep it from ``metric``."""

    status: str
    blocking_entities: tuple[str, ...] = ()


def is_calibrated(calibration_method: str | None) -> bool:
    """Whether an accepted calibration exists and its method is not ``none``."""
    return calibration_method is not None and calibration_method != NO_CALIBRATION_METHOD


def is_relevant(entity: MetricEntity) -> bool:
    """Whether ``entity`` takes part in the metric status (structure, access, evidence)."""
    return not entity.deleted and (entity.layer in METRIC_LAYERS or entity.references_evidence)


def is_blocking(entity: MetricEntity) -> bool:
    """Whether ``entity`` keeps the plan from ``metric``; H3 refines this rule."""
    return is_relevant(entity) and entity.metric_origin not in METRIC_ORIGINS


def compute_metric_status(
    source_mode: str,
    calibration_method: str | None,
    as_metric: bool,
    entities: Iterable[MetricEntity],
) -> MetricStatusResult:
    """Metric status of a plan under the initial rule set of the module docstring.

    ``calibration_method`` is the method of the plan's active accepted calibration, or
    ``None`` when there is none. Deleted entities are ignored. ``blocking_entities`` keeps
    the input order.
    """
    if not is_calibrated(calibration_method):
        return MetricStatusResult(NON_METRIC)
    relevant = [entity for entity in entities if is_relevant(entity)]
    blocking = tuple(entity.id for entity in relevant if is_blocking(entity))
    if blocking:
        return MetricStatusResult(APPROXIMATE, blocking)
    if relevant or (as_metric and source_mode == SCALED_DRAWING_MODE):
        return MetricStatusResult(METRIC)
    return MetricStatusResult(APPROXIMATE)


def default_metric_origin(
    source_mode: str,
    calibration_method: str | None,
    as_metric: bool,
    entity_type: str,
) -> str:
    """``metric_origin`` of an entity drawn by hand (spec section 11.3).

    ``scaled_drawing`` in a ``scaled_drawing`` plan whose active calibration was accepted as
    metric; otherwise ``none`` (a hand-placed entity without a dimension). ``entity_type``
    is not used by the initial rule set; it is the H3 extension point.
    """
    del entity_type  # H3 extension point
    if source_mode == SCALED_DRAWING_MODE and is_calibrated(calibration_method) and as_metric:
        return ORIGIN_SCALED_DRAWING
    return ORIGIN_NONE
