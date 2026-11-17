"""Metric status of a plan and provenance defaults of new entities (spec 11.3, REQ-07, US-11).

Pure functions with no database, FastAPI or I/O: callers pass the plan's ``source_mode``,
its active calibration (method and ``as_metric``) and lightweight entity inputs. Values
are the plain strings stored in the database, so ``StrEnum`` members of
``sketch2cad.db.models`` can be passed and compared directly.

Rule set (H2, extended in H3):

- A plan without an accepted calibration, or calibrated with method ``none``, is
  ``non_metric`` and lists no blocking entities: the calibration blocks it, not an entity.
- Otherwise every live entity in ``L_STRUCTURE`` or ``L_ACCESS``, or referencing an
  evidence item, whose ``metric_origin`` is not metric (``trace_only``, ``none``) blocks
  ``metric``; any blocking entity makes the plan ``approximate``. Its reason code
  (:func:`blocking_reason`) is that ``metric_origin``.
- With no blocking entity the plan is ``metric`` when some relevant entity exists (all of
  them carry a metric origin), or, when there is none, only for a ``scaled_drawing`` whose
  calibration was accepted as metric: a ``schematic_sketch`` gets its metric truth from
  dimensions, so an empty one stays ``approximate``.

- A declared ``dimension`` whose declared and measured values disagree (``discrepancy``)
  is reported in ``discrepant_dimensions`` but does not change the status: spec section
  11.3 only marks it, the status depends on ``metric_origin`` alone, and ``L_DIMENSIONS``
  is not a metric layer.
- An entity whose declared segment lengths were broken by a ``fix_length`` of a connected
  entity (``length_conflict``) is reported in ``length_conflicts``; like a discrepancy it
  does not change the status (the declaration was dropped, so the entity's own
  ``metric_origin`` already counts).

Hand-drawn entities get ``scaled_drawing`` in a ``scaled_drawing`` plan whose calibration
was accepted as metric, otherwise ``none`` (:func:`default_metric_origin`). Suggestions are
born ``trace_only`` and are promoted only when a person accepts or corrects them, by the
same condition (:func:`reviewed_metric_origin`); otherwise they stay ``trace_only``. The H3 tools
assign the dimension-based origins (``declared_dimension``, ``triangulation``,
``baseline_offset``) on the entity itself, so the rule reads them as stored.

:func:`apply_metric_status` is the only place that writes a plan's ``metric_status``: it
takes any object with that attribute (the ORM ``Plan``) and a converter to its stored type.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

METRIC: Final = "metric"
APPROXIMATE: Final = "approximate"
NON_METRIC: Final = "non_metric"

SCALED_DRAWING_MODE: Final = "scaled_drawing"
NO_CALIBRATION_METHOD: Final = "none"

ORIGIN_SCALED_DRAWING: Final = "scaled_drawing"
ORIGIN_NONE: Final = "none"
ORIGIN_TRACE_ONLY: Final = "trace_only"
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
    discrepancy: bool = False
    length_conflict: bool = False


@dataclass(frozen=True)
class MetricStatusResult:
    """Metric status of a plan and the ids of the entities that keep it from ``metric``.

    ``reasons`` pairs each blocking id with its :func:`blocking_reason`, in the order of
    ``blocking_entities``; ``discrepant_dimensions`` lists the live dimensions flagged with
    a discrepancy and ``length_conflicts`` the live entities with broken declared lengths.
    These are derived details and take no part in equality.
    """

    status: str
    blocking_entities: tuple[str, ...] = ()
    reasons: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    discrepant_dimensions: tuple[str, ...] = field(default=(), compare=False)
    length_conflicts: tuple[str, ...] = field(default=(), compare=False)


class MetricStatusHolder(Protocol):
    """Anything that stores a metric status, such as the ORM ``Plan``.

    The attribute is ``Any`` because an ORM column is typed as its descriptor.
    """

    metric_status: Any


def is_calibrated(calibration_method: str | None) -> bool:
    """Whether an accepted calibration exists and its method is not ``none``."""
    return calibration_method is not None and calibration_method != NO_CALIBRATION_METHOD


def is_relevant(entity: MetricEntity) -> bool:
    """Whether ``entity`` takes part in the metric status (structure, access, evidence)."""
    return not entity.deleted and (entity.layer in METRIC_LAYERS or entity.references_evidence)


def blocking_reason(entity: MetricEntity) -> str | None:
    """Why ``entity`` keeps the plan from ``metric``, or ``None`` when it does not.

    The reason code is the entity's non-metric ``metric_origin`` (``trace_only`` or
    ``none``) for a relevant live entity.
    """
    if not is_relevant(entity) or entity.metric_origin in METRIC_ORIGINS:
        return None
    return entity.metric_origin


def is_blocking(entity: MetricEntity) -> bool:
    """Whether ``entity`` keeps the plan from ``metric`` (:func:`blocking_reason`)."""
    return blocking_reason(entity) is not None


def compute_metric_status(
    source_mode: str,
    calibration_method: str | None,
    as_metric: bool,
    entities: Iterable[MetricEntity],
) -> MetricStatusResult:
    """Metric status of a plan under the rule set of the module docstring.

    ``calibration_method`` is the method of the plan's active accepted calibration, or
    ``None`` when there is none. Deleted entities are ignored. ``blocking_entities`` keeps
    the input order.
    """
    live = [entity for entity in entities if not entity.deleted]
    discrepant = tuple(entity.id for entity in live if entity.discrepancy)
    conflicts = tuple(entity.id for entity in live if entity.length_conflict)
    if not is_calibrated(calibration_method):
        return MetricStatusResult(
            NON_METRIC, discrepant_dimensions=discrepant, length_conflicts=conflicts
        )
    relevant = [entity for entity in live if is_relevant(entity)]
    reasons = tuple(
        (entity.id, reason)
        for entity in relevant
        if (reason := blocking_reason(entity)) is not None
    )
    if reasons:
        blocking = tuple(entity_id for entity_id, _ in reasons)
        return MetricStatusResult(APPROXIMATE, blocking, reasons, discrepant, conflicts)
    if relevant or (as_metric and source_mode == SCALED_DRAWING_MODE):
        return MetricStatusResult(METRIC, (), (), discrepant, conflicts)
    return MetricStatusResult(APPROXIMATE, (), (), discrepant, conflicts)


def apply_metric_status[T](
    holder: MetricStatusHolder, result: MetricStatusResult, convert: Callable[[str], T]
) -> T:
    """Store ``result.status`` in ``holder.metric_status`` through ``convert``; return it.

    The single write of a plan's metric status: callers pass the ORM ``Plan`` and its enum
    (``MetricStatus``) as ``convert``.
    """
    value = convert(result.status)
    holder.metric_status = value
    return value


def is_metric_scaled_drawing(
    source_mode: str, calibration_method: str | None, as_metric: bool
) -> bool:
    """Whether drawn geometry is metric by itself: a ``scaled_drawing`` plan whose active
    calibration (not ``none``) was accepted as metric (spec section 11.3)."""
    return source_mode == SCALED_DRAWING_MODE and is_calibrated(calibration_method) and as_metric


def default_metric_origin(
    source_mode: str,
    calibration_method: str | None,
    as_metric: bool,
    entity_type: str,
) -> str:
    """``metric_origin`` of an entity drawn by hand (spec section 11.3).

    ``scaled_drawing`` in a ``scaled_drawing`` plan whose active calibration was accepted as
    metric; otherwise ``none`` (a hand-placed entity without a dimension), whatever the
    ``entity_type``: the H3 tools set the dimension-based origins afterwards.
    """
    del entity_type  # same default for every type (spec section 11.3)
    if is_metric_scaled_drawing(source_mode, calibration_method, as_metric):
        return ORIGIN_SCALED_DRAWING
    return ORIGIN_NONE


def reviewed_metric_origin(
    source_mode: str,
    calibration_method: str | None,
    as_metric: bool,
    entity_type: str,
) -> str:
    """``metric_origin`` of a suggestion once a person accepts or corrects it (spec 11.3).

    ``scaled_drawing`` under the same condition as :func:`default_metric_origin`;
    otherwise the suggestion keeps ``trace_only`` (a ``schematic_sketch`` has no promotion:
    only a dimension makes it metric).
    """
    del entity_type  # same rule for every type (spec section 11.3)
    if is_metric_scaled_drawing(source_mode, calibration_method, as_metric):
        return ORIGIN_SCALED_DRAWING
    return ORIGIN_TRACE_ONLY
