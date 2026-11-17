"""Hand-built revision snapshots in the shape of ``revisions.snapshot.build_snapshot``."""

from datetime import UTC, datetime
from typing import Any

# ``revisions.frozen_at`` of the revision the snapshots stand for (not part of the snapshot).
FROZEN_AT = datetime(2026, 10, 21, 9, 30, 15, 123456, tzinfo=UTC)
PLAN_ID = "7a0c4b1e-0d2f-4c3a-9e8b-1f2a3b4c5d6e"
USER_ID = "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"


def entity(
    number: int, kind: str, layer: str, geometry: dict[str, Any], revision_number: int = 2
) -> dict[str, Any]:
    """One snapshot entity (spec section 8.2); ``number`` makes a sortable fixed UUID."""
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "type": kind,
        "layer": layer,
        "geometry": {"kind": kind, **geometry},
        "image_region": None,
        "attributes": {},
        "evidence_id": None,
        "constraint": None,
        "source": {"method": "manual"},
        "confidence": None,
        "model_version": None,
        "review_status": "accepted",
        "metric_origin": "scaled_drawing",
        "created_by": USER_ID,
        "revision_number": revision_number,
    }


def snapshot(entities: list[dict[str, Any]], paper_scale: int | None = 100) -> dict[str, Any]:
    """A snapshot of revision 2 of a calibrated plan holding ``entities``."""
    return {
        "schema_version": "2.0",
        "plan_id": PLAN_ID,
        "revision_number": 2,
        "source_mode": "scaled_drawing",
        "metric_status": "metric",
        "calibration": {
            "id": "1c2d3e4f-5061-4728-93a4-b5c6d7e8f901",
            "method": "scan_segment",
            "image_to_paper": [[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 1.0]],
            "control_points": [],
            "reprojection_error_mm": 0.2,
            "paper_scale": paper_scale,
            "as_metric": True,
            "accepted_by": USER_ID,
            "accepted_at": "2026-10-20T10:00:00.000000Z",
        },
        "local_frame": None,
        "catalog_version": "default-1",
        "entities": sorted(entities, key=lambda item: item["id"]),
        "evidence_table": [],
    }


def default_entities() -> list[dict[str, Any]]:
    """Lines, polylines (one closed), points and one text, on five layers."""
    return [
        entity(1, "line", "L_STRUCTURE", {"coords_m": [[0.0, 0.0], [4.25, 0.0]]}),
        entity(2, "line", "L_STRUCTURE", {"coords_m": [[4.25, 0.0], [4.25, 3.125]]}),
        entity(
            3,
            "polyline",
            "L_STRUCTURE",
            {"coords_m": [[-1.5, -2.0], [6.0, -2.0], [6.0, 5.0], [-1.5, 5.0]], "closed": True},
        ),
        entity(
            4,
            "polyline",
            "L_ACCESS",
            {"coords_m": [[1.0, 3.125], [1.9, 3.125], [1.9, 3.5]], "closed": False},
        ),
        entity(5, "point", "L_EVIDENCE", {"coords_m": [[2.123456789, 1.987654321]]}),
        entity(6, "point", "L_EVIDENCE", {"coords_m": [[3.0, 0.5]]}),
        entity(7, "point", "L_CONTROL", {"coords_m": [[0.0, 0.0]]}),
        entity(
            8,
            "text",
            "L_TEXT",
            {
                "anchor_m": [1.0, 1.0],
                "rotation_deg": 0.0,
                "height_mm_paper": 2.5,
                "content": "Habitación 1",
            },
        ),
    ]


def mixed_entities() -> list[dict[str, Any]]:
    """:func:`default_entities` plus a symbol block and an aligned dimension (``*D`` block)."""
    door = entity(
        9,
        "symbol",
        "L_SYMBOLS",
        {
            "anchor_m": [1.0, 2.0],
            "rotation_deg": 90.0,
            "scale": 1.0,
            "symbol_id": "door",
            "attributes": {},
        },
    )
    dimension = entity(
        10,
        "dimension",
        "L_DIMENSIONS",
        {
            "p1_m": [0.0, 0.0],
            "p2_m": [4.25, 0.0],
            "offset_m": 0.5,
            "declared_value_m": 4.24,
            "measured_value_m": 4.25,
        },
    )
    return [*default_entities(), door, dimension]
