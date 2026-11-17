"""Share of suggested polylines accepted unchanged, from the event log (task 001-118, G-3).

    DATABASE_URL=postgresql+psycopg://... \\
    uv run --project sketch2cad-api python validation/query_suggestion_acceptance.py \\
        --plan-id <uuid> [--sketch <name>]

Read-only: one ``SELECT`` on ``event`` for the plan. Each polyline listed in a
``suggestions.added`` event (``after.entity_ids``) ends in exactly one outcome:

* ``accepted_unchanged``: ``entity.reviewed`` with ``after.review_status = accepted``
  (bulk review) and never edited, before or after;
* ``corrected``: an ``entity.updated`` while suggested (its ``after.review_status`` turns
  ``corrected``) or after being accepted;
* ``rejected``: ``entity.reviewed`` to ``rejected`` (bulk) or ``entity.deleted`` with
  ``after.review_status = rejected`` (DELETE of a pending suggestion);
* ``removed_after_review``: deleted after being accepted or corrected;
* ``cleared``: still pending when a recalibration or relaunch removed it (``entity.deleted``
  without ``entity_id``, ids in ``before.entity_ids``); ``pending``: never reviewed.

The percentage is accepted_unchanged over the reviewed ones (the first four outcomes).
Copies made by reopening a revision get new ids and are not followed: measure before
reopening. ``--sketch`` prints a ready row prefix for validation/suggestions.csv.
"""

from __future__ import annotations  # also runs on an older system Python

import argparse
import os
import sys
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

KINDS = ("suggestions.added", "entity.updated", "entity.reviewed", "entity.deleted")
REVIEWED = ("accepted_unchanged", "corrected", "rejected", "removed_after_review")
QUERY = (
    "SELECT kind, entity_id, before, after FROM event "
    "WHERE plan_id = :plan_id AND kind IN :kinds ORDER BY id"
)


@dataclass(frozen=True)
class Tally:
    """Outcome counts of the suggested polylines of one plan."""

    counts: dict[str, int]

    @property
    def suggested(self) -> int:
        return sum(self.counts.values())

    @property
    def reviewed(self) -> int:
        return sum(self.counts.get(outcome, 0) for outcome in REVIEWED)

    @property
    def accepted_unchanged_pct(self) -> float | None:
        if not self.reviewed:
            return None
        return 100.0 * self.counts.get("accepted_unchanged", 0) / self.reviewed


def tally_suggestions(events: Iterable[Mapping[str, Any]]) -> Tally:
    """Outcome of every suggested polyline; ``events`` in log order (``event.id``)."""
    state: dict[str, str] = {}
    for event in events:
        kind, before, after = event["kind"], event["before"] or {}, event["after"] or {}
        entity = None if event["entity_id"] is None else str(event["entity_id"])
        status = after.get("review_status")
        if kind == "suggestions.added":
            state.update(dict.fromkeys(map(str, after.get("entity_ids", [])), "pending"))
        elif kind == "entity.deleted" and entity is None:
            for cleared in map(str, before.get("entity_ids", [])):
                if state.get(cleared) == "pending":
                    state[cleared] = "cleared"
        elif entity not in state:
            continue
        elif kind == "entity.updated" and state[entity] in ("pending", "accepted_unchanged"):
            state[entity] = "corrected"
        elif kind == "entity.reviewed" and status == "rejected":
            state[entity] = "rejected"
        elif kind == "entity.reviewed" and status == "accepted" and state[entity] == "pending":
            state[entity] = "accepted_unchanged"
        elif kind == "entity.deleted":
            rejected = status == "rejected" or state[entity] == "pending"
            state[entity] = "rejected" if rejected else "removed_after_review"
    counts: dict[str, int] = {}
    for outcome in state.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return Tally(counts)


def fetch_events(connection: Any, plan_id: uuid.UUID) -> list[dict[str, Any]]:
    """The plan's review-related events, oldest first (``connection``: SQLAlchemy)."""
    from sqlalchemy import bindparam, text

    query = text(QUERY).bindparams(bindparam("kinds", expanding=True))
    result = connection.execute(query, {"plan_id": plan_id, "kinds": list(KINDS)})
    return [dict(row._mapping) for row in result]


def report(tally: Tally, sketch: str | None) -> list[str]:
    pct = tally.accepted_unchanged_pct
    lines = [f"suggested polylines: {tally.suggested}, reviewed: {tally.reviewed}"]
    lines += [f"  {outcome}: {count}" for outcome, count in sorted(tally.counts.items())]
    lines.append(
        "accepted unchanged: " + ("n/a (none reviewed)" if pct is None else f"{pct:.1f} %")
    )
    if sketch is not None and pct is not None:
        accepted = tally.counts.get("accepted_unchanged", 0)
        lines.append(f"suggestions.csv prefix: {sketch},{tally.reviewed},{accepted},{pct:.1f}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G-3: suggestions accepted unchanged.")
    parser.add_argument("--plan-id", type=uuid.UUID, required=True)
    parser.add_argument("--sketch", help="sketch file name for the suggestions.csv row")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            tally = tally_suggestions(fetch_events(connection, args.plan_id))
    finally:
        engine.dispose()
    print("\n".join(report(tally, args.sketch)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
