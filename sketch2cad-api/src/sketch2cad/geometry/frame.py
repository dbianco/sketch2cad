"""Local coordinate frame of a plan and rigid transforms between frames (spec section 11.1).

A local frame is set on paper: its origin is a paper point in mm (Y down) and its +X axis
makes ``x_axis_angle_deg`` with the world +X axis, counter-clockwise positive in the world
(Y up). The world is paper→world (``m = mm × paper_scale / 1000``, Y inverted), so local
coordinates of a world point ``w`` are ``R(−θ) (w − o)`` with ``o`` the world position of
the origin. The North angle is metadata only and never moves the model.

Between two frames ``a`` and ``b`` local coordinates change by one rigid transform
``p_b = R(θ_a − θ_b) p_a + R(−θ_b) (o_a − o_b)``; :class:`RigidTransform` holds it as
``(dx_m, dy_m, theta_deg)`` meaning ``p' = R(theta) p + (dx, dy)``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.transforms import paper_to_world

FULL_TURN_DEG = 360.0


def normalize_deg(angle_deg: float) -> float:
    """``angle_deg`` reduced to ``[0, 360)``; raises ``ValueError`` when it is not finite."""
    if not math.isfinite(angle_deg):
        raise ValueError(f"angle must be finite, got {angle_deg}")
    reduced = float(angle_deg) % FULL_TURN_DEG
    return 0.0 if reduced == FULL_TURN_DEG else reduced


def _cos_sin(angle_deg: float) -> tuple[float, float]:
    """Cosine and sine of ``angle_deg``, exact for multiples of 90 degrees."""
    quarter, rest = divmod(normalize_deg(angle_deg), 90.0)
    if rest == 0.0:
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarter)]
    radians = math.radians(angle_deg)
    return math.cos(radians), math.sin(radians)


def _rotate(point: Point, cos_sin: tuple[float, float]) -> Point:
    c, s = cos_sin
    x, y = point
    return (c * x - s * y, s * x + c * y)


@dataclass(frozen=True)
class LocalFrame:
    """The ``calibration.local_frame`` JSON: origin on paper (mm), +X and North angles.

    Angles are stored reduced to ``[0, 360)`` so that equal frames compare equal.
    """

    origin_paper_mm: Point
    x_axis_angle_deg: float = 0.0
    north_angle_deg: float = 0.0

    @classmethod
    def create(
        cls, origin_paper_mm: Sequence[float], x_axis_angle_deg: float, north_angle_deg: float
    ) -> "LocalFrame":
        """A frame with finite coordinates and normalized angles, else ``ValueError``."""
        if len(origin_paper_mm) != 2 or not all(math.isfinite(v) for v in origin_paper_mm):
            raise ValueError("origin_paper_mm must be two finite numbers")
        x, y = origin_paper_mm
        return cls(
            (float(x), float(y)), normalize_deg(x_axis_angle_deg), normalize_deg(north_angle_deg)
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LocalFrame":
        return cls.create(
            data["origin_paper_mm"], data["x_axis_angle_deg"], data["north_angle_deg"]
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "origin_paper_mm": [self.origin_paper_mm[0], self.origin_paper_mm[1]],
            "x_axis_angle_deg": self.x_axis_angle_deg,
            "north_angle_deg": self.north_angle_deg,
        }

    def same_placement(self, other: "LocalFrame") -> bool:
        """Whether both frames share origin and +X axis (they may differ only in North)."""
        return (
            self.origin_paper_mm == other.origin_paper_mm
            and self.x_axis_angle_deg == other.x_axis_angle_deg
        )


DEFAULT_FRAME = LocalFrame((0.0, 0.0), 0.0, 0.0)
"""Frame of coordinates drawn before any local frame is set: paper origin, world axes."""


@dataclass(frozen=True)
class WorldFrame:
    """Placement of a local frame in the world: origin in metres and +X axis angle."""

    origin_m: Point
    x_axis_angle_deg: float


def world_frame(frame: LocalFrame, paper_scale: float) -> WorldFrame:
    """World placement of ``frame`` on a 1:``paper_scale`` paper (paper→world inverts Y)."""
    return WorldFrame(paper_to_world(frame.origin_paper_mm, paper_scale), frame.x_axis_angle_deg)


@dataclass(frozen=True)
class RigidTransform:
    """``p' = R(theta) p + (dx_m, dy_m)``: a rotation about the origin, then a translation."""

    dx_m: float
    dy_m: float
    theta_deg: float

    def apply_point(self, point: Sequence[float]) -> Point:
        x, y = _rotate((float(point[0]), float(point[1])), _cos_sin(self.theta_deg))
        return (x + self.dx_m, y + self.dy_m)

    def apply_points(self, points: Sequence[Sequence[float]]) -> list[Point]:
        return [self.apply_point(point) for point in points]

    def apply_angle(self, angle_deg: float) -> float:
        """An orientation after the rotation, reduced to ``[0, 360)``; unchanged if theta is 0."""
        if self.theta_deg == 0.0:
            return angle_deg
        return normalize_deg(angle_deg + self.theta_deg)

    def to_json(self) -> dict[str, float]:
        return {"dx_m": self.dx_m, "dy_m": self.dy_m, "theta_deg": self.theta_deg}


def rigid_between(old: WorldFrame, new: WorldFrame) -> RigidTransform:
    """The transform taking local coordinates in ``old`` to local coordinates in ``new``.

    ``theta`` is ``θ_old − θ_new`` reduced to ``[−180, 180]``; the translation is
    ``R(−θ_new) (o_old − o_new)``.
    """
    theta = math.remainder(old.x_axis_angle_deg - new.x_axis_angle_deg, FULL_TURN_DEG)
    delta = (old.origin_m[0] - new.origin_m[0], old.origin_m[1] - new.origin_m[1])
    dx, dy = _rotate(delta, _cos_sin(-new.x_axis_angle_deg))
    return RigidTransform(dx, dy, theta + 0.0)
