"""Typed shapes of ``entity.constraint`` (spec sections 8.2 and 11.3)."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from sketch2cad.entities.constraints import (
    BaselineOffsetConstraint,
    TriangulationConstraint,
    constraint_to_json,
    parse_constraint,
)

REF_A = str(uuid.uuid4())
REF_B = str(uuid.uuid4())
TRIANGULATION: dict[str, Any] = {
    "kind": "triangulation",
    "refs": [REF_A, REF_B],
    "params": {"d_a_m": 2.10, "d_b_m": 3.45, "solution": "nearest"},
}
BASELINE_OFFSET: dict[str, Any] = {
    "kind": "baseline_offset",
    "refs": [REF_A, REF_B],
    "params": {"s_m": 1.5, "d_m": 0.8, "side": "left"},
}


def test_triangulation_sample_from_the_spec_is_valid_and_round_trips() -> None:
    constraint = parse_constraint(TRIANGULATION)

    assert isinstance(constraint, TriangulationConstraint)
    assert constraint.params.solution == "nearest"
    assert constraint_to_json(constraint) == TRIANGULATION


def test_baseline_offset_sample_is_valid_and_round_trips() -> None:
    constraint = parse_constraint(BASELINE_OFFSET)

    assert isinstance(constraint, BaselineOffsetConstraint)
    assert constraint.params.side == "left"
    assert constraint_to_json(constraint) == BASELINE_OFFSET


def test_baseline_offset_accepts_a_point_behind_a_and_on_the_baseline() -> None:
    value = {**BASELINE_OFFSET, "params": {"s_m": -0.5, "d_m": 0.0, "side": "right"}}

    assert constraint_to_json(parse_constraint(value)) == value


def with_params(base: dict[str, Any], **params: Any) -> dict[str, Any]:
    return {**base, "params": {**base["params"], **params}}


@pytest.mark.parametrize(
    "value",
    [
        {**TRIANGULATION, "kind": "circle"},
        {key: value for key, value in TRIANGULATION.items() if key != "refs"},
        {**TRIANGULATION, "refs": [REF_A]},
        {**TRIANGULATION, "refs": [REF_A, REF_B, str(uuid.uuid4())]},
        {**TRIANGULATION, "refs": [REF_A, REF_A]},
        {**TRIANGULATION, "refs": [REF_A, "not-a-uuid"]},
        {**TRIANGULATION, "extra": True},
        with_params(TRIANGULATION, d_a_m=0),
        with_params(TRIANGULATION, d_b_m=-1.0),
        with_params(TRIANGULATION, d_a_m="2.1"),
        with_params(TRIANGULATION, d_a_m=float("inf")),
        with_params(TRIANGULATION, solution="farthest"),
        {**TRIANGULATION, "params": BASELINE_OFFSET["params"]},
        with_params(BASELINE_OFFSET, d_m=-0.1),
        with_params(BASELINE_OFFSET, side="up"),
        with_params(BASELINE_OFFSET, s_m=float("nan")),
        with_params(BASELINE_OFFSET, d_a_m=1.0),
    ],
    ids=[
        "unknown_kind",
        "missing_refs",
        "one_ref",
        "three_refs",
        "same_ref_twice",
        "ref_not_uuid",
        "extra_field",
        "zero_distance",
        "negative_distance",
        "string_distance",
        "infinite_distance",
        "unknown_solution",
        "params_of_other_kind",
        "negative_offset",
        "unknown_side",
        "nan_along",
        "extra_param",
    ],
)
def test_invalid_constraints_are_rejected(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse_constraint(value)
