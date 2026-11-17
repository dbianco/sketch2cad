"""G-1, G-2 and G-3 computations of the validation set (tasks 001-116..118, REQ-20).

The scripts live outside the package (``validation/``) and are loaded by path. The event
shapes below mirror ``entities/bulk_review.py`` (``entity.reviewed``),
``entities/service.py`` (``entity.updated`` / ``entity.deleted``) and
``worker/tasks_suggest.py`` (``suggestions.added`` and the clearing ``entity.deleted``).
"""

import importlib.util
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from sqlalchemy import Engine, insert

from sketch2cad.db.base import Base
from sketch2cad.db.models import Event

VALIDATION = Path(__file__).resolve().parents[3] / "validation"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"validation_{name}", VALIDATION / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"validation_{name}"] = module
    spec.loader.exec_module(module)
    return module


_g1, _g2, _g3 = (_load(name) for name in ("compute_g1", "compute_g2", "compute_g3"))
_query = _load("query_suggestion_acceptance")
Main = Callable[[list[str]], int]
g1_main, g2_main, g3_main = (cast(Main, m.main) for m in (_g1, _g2, _g3))
dimension_match = cast(Callable[[str, str], bool], _g2.dimension_match)
tally_suggestions = cast(Callable[[list[dict[str, Any]]], Any], _query.tally_suggestions)
fetch_events = cast(Callable[[Any, uuid.UUID], list[dict[str, Any]]], _query.fetch_events)


def _csv(tmp_path: Path, text: str) -> str:
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("name", ["timings.csv", "dimension_check.csv", "suggestions.csv"])
def test_committed_templates_hold_no_data_rows(name: str) -> None:
    lines = (VALIDATION / name).read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if not line.startswith("#")]) == 1  # the header only


def test_g1_reports_medians_and_ratio(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    header = ",".join(cast(tuple[str, ...], _g1.COLUMNS))
    rows = ["c1,schematic,freecad,20,40,,,,", "c2,triangulation,sketch2cad,30,30,,,,"]
    rows.append("c3,baseline_offset,freecad,abandoned,50,,,,")

    assert g1_main([_csv(tmp_path, "\n".join([header, *rows]) + "\n")]) == 0
    out = capsys.readouterr().out
    assert "with both times: 2, incomplete: 1" in out and "FreeCAD median: 40.00" in out
    assert "ratio Sketch2CAD/FreeCAD median: 0.75 (IQR 0.62-0.88)" in out and "c1" not in out


def test_g1_template_without_rows_exits_1() -> None:
    assert g1_main([str(VALIDATION / "timings.csv")]) == 1


@pytest.mark.parametrize(
    ("written", "exported", "expected"),
    [
        ("4,20", "4.20", True),
        ("4,2", "4.20", True),
        ("4,20", "4.2", True),
        ("4,25", "4.254", True),
        ("4,25", "4.24", False),
        ("4,25", "4.3", False),
        ("12", "12.00", True),
        ("3,5", "3.45", True),  # half-up at the written precision
        ("3,5", "3.44", False),
    ],
)
def test_dimension_match_rule(written: str, exported: str, expected: bool) -> None:
    assert dimension_match(written, exported) is expected


def test_g2_needs_the_reviewer_initials(tmp_path: Path) -> None:
    assert g2_main([str(VALIDATION / "dimension_check.csv")]) == 1  # template: no reviewer
    header = ",".join(cast(tuple[str, ...], _g2.COLUMNS))
    assert g2_main([_csv(tmp_path, f"{header}\nc1,d1,4,4.00,yes,\n")]) == 1


def test_g2_percentage_target_and_flag_disagreement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    header = ",".join(cast(tuple[str, ...], _g2.COLUMNS))
    rows = [f'c1,d{i},"4,20",4.20,yes,' for i in range(1, 99)] + ['c2,d99,"3,10",3.01,yes,']
    text = "# reviewer: AB\n" + "\n".join([header, *rows, 'c2,d100,"2,5",2.50,,']) + "\n"

    assert g2_main([_csv(tmp_path, text)]) == 0
    out = capsys.readouterr().out
    assert "reviewer: AB" in out and "dimensions: 100, matching: 99, mismatching: 1" in out
    assert "G-2 match: 99.0 % (target >= 99 %: met)" in out
    assert "row 101: reviewer flag 'yes' differs" in out and "c2" not in out


def test_g3_pooled_and_median_and_consistency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    header = ",".join(cast(tuple[str, ...], _g3.COLUMNS))
    good = f"{header}\nc1,10,8,80.0,12,20,\nc2,30,15,50.0,30,30,\n"

    assert g3_main([_csv(tmp_path, good)]) == 0
    out = capsys.readouterr().out
    assert "accepted unchanged (pooled): 57.5 %" in out and "median %: 65.00" in out
    assert g3_main([_csv(tmp_path, f"{header}\nc1,10,8,75.0,12,20,\n")]) == 1
    assert g3_main([str(VALIDATION / "suggestions.csv")]) == 1


def test_g3_declares_h5_not_built(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert g3_main([_csv(tmp_path, "# H5 no construido\n")]) == 0
    assert "validación posterior" in capsys.readouterr().out


def _event(kind: str, entity: str | None, before: Any, after: Any) -> dict[str, Any]:
    return {"kind": kind, "entity_id": entity, "before": before, "after": after}


def _events(ids: list[str]) -> list[dict[str, Any]]:
    a, b, c, d, e, f, _ = ids  # the last one is never reviewed
    suggested = {"review_status": "suggested"}
    accepted = {"review_status": "accepted", "metric_origin": "trace_only", "deleted": False}
    rejected = {"review_status": "rejected", "metric_origin": "trace_only", "deleted": True}
    return [
        _event("suggestions.added", None, None, {"entity_ids": ids, "count": len(ids)}),
        # a: bulk-accepted unchanged.  b: edited while suggested.
        _event("entity.reviewed", a, suggested, accepted),
        _event("entity.updated", b, suggested, {"review_status": "corrected"}),
        # c: bulk-rejected.  d: DELETE of a pending suggestion.  e: accepted then deleted.
        _event("entity.reviewed", c, suggested, rejected),
        _event("entity.deleted", d, {}, {"review_status": "rejected"}),
        _event("entity.reviewed", e, suggested, accepted),
        _event("entity.deleted", e, {}, None),
        # f: cleared by a relaunch.  g: never reviewed.  A manual entity is ignored.
        _event("entity.deleted", None, {"entity_ids": [f], "count": 1}, None),
        _event("entity.updated", str(uuid.uuid4()), {}, {}),
    ]


def test_tally_classifies_every_suggestion_outcome() -> None:
    tally = tally_suggestions(_events([str(uuid.uuid4()) for _ in range(7)]))

    assert tally.counts == {
        "accepted_unchanged": 1,
        "corrected": 1,
        "rejected": 2,
        "removed_after_review": 1,
        "cleared": 1,
        "pending": 1,
    }
    assert (tally.suggested, tally.reviewed, tally.accepted_unchanged_pct) == (7, 5, 20.0)


def test_editing_an_accepted_suggestion_counts_as_corrected() -> None:
    entity = str(uuid.uuid4())
    events = _events([entity, *(str(uuid.uuid4()) for _ in range(6))])
    events.append({"kind": "entity.updated", "entity_id": entity, "before": {}, "after": {}})

    assert tally_suggestions(events).counts["corrected"] == 2


@pytest.fixture
def event_table(db_engine: Engine) -> Iterator[Engine]:
    db_engine.dispose()
    Base.metadata.create_all(db_engine)
    try:
        yield db_engine
    finally:
        Base.metadata.drop_all(db_engine)


def test_fetch_events_reads_the_plan_events_in_log_order(event_table: Engine) -> None:
    plan_id, other_plan = uuid.uuid4(), uuid.uuid4()
    ids = [uuid.uuid4() for _ in range(7)]
    rows = _events([str(i) for i in ids])
    # Another plan's rejection of the accepted entity must not be read.
    foreign = {**rows[1], "after": {"review_status": "rejected"}}
    stamp = {"occurred_at": datetime.now(UTC), "prev_hash": "0" * 64, "hash": "0" * 64}
    with event_table.begin() as connection:
        for plan, row in [*((plan_id, r) for r in rows), (other_plan, foreign)]:
            entity = None if row["entity_id"] is None else uuid.UUID(str(row["entity_id"]))
            values = {**row, **stamp, "entity_id": entity, "plan_id": plan}
            connection.execute(insert(Event).values(**values))
        events = fetch_events(connection, plan_id)

    assert [e["kind"] for e in events] == [r["kind"] for r in rows]
    assert tally_suggestions(events).accepted_unchanged_pct == 20.0
