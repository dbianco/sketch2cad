"""Table-driven plan state machine (spec section 9.2; REQ-10, US-14, V-09).

:data:`TRANSITIONS` is the section 9.2 table: every allowed ``(from, to)`` pair with who
performs it, whether it needs a reason, its side effect and the automatic step that
follows it. Any pair outside the table is illegal (409), including ``from == to``.

Rules without roles are system transitions performed by another action and never on
request: ``CREATED -> CALIBRATION_PENDING`` (plan creation), ``CALIBRATION_PENDING ->
EDITING`` (calibration accept, :func:`sketch2cad.calibration.service.accept_calibration`),
``VALIDATED -> EXPORTED`` (final export) and the automatic ``REJECTED -> EDITING`` and
``REOPENED -> EDITING`` that complete a rejection and a reopening. So a plan never rests in
``REJECTED`` or ``REOPENED``: each step writes its own ``plan.transition`` event.

Role sets follow spec section 3: a ``reviewer`` may do everything a ``technician`` may;
``admin`` edits no plan. On top of the role, every transition requires the ``edit``
permission on the plan's case file (:data:`TRANSITION_PERMISSION`).

:func:`transition` checks, in order: the pair is in the table (409), it is requestable
(409), the user's role (403, V-09 for ``VALIDATED``), the reason (422, V-09); then it
applies the effect, sets the status and appends ``plan.transition``. ``metric_status`` is
never touched.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.cases.permissions import CasePermission
from sketch2cad.db.models import Plan, PlanStatus, Revision, User, UserRole
from sketch2cad.plans.lease import LeaseConflictError, acquire_lease, release_lease
from sketch2cad.revisions.service import RevisionConflictError, freeze, reopen

logger = logging.getLogger(__name__)

TRANSITION_PERMISSION = CasePermission.EDIT
"""Case permission every requested transition needs, besides the role of its rule."""

V09_VALIDATE_MESSAGE = "Solo un revisor puede validar este plano."
V09_REASON_MESSAGE = "Indica el motivo del rechazo."
ILLEGAL_CODE = "illegal_transition"
SYSTEM_CODE = "system_transition"


class Effect(StrEnum):
    """Side effect of a transition, applied before the status changes."""

    NONE = "none"
    RELEASE_LEASE = "release_lease"
    ACQUIRE_LEASE = "acquire_lease"
    FREEZE = "freeze"
    REOPEN = "reopen"


TECHNICIAN_ROLES = frozenset({UserRole.TECHNICIAN, UserRole.REVIEWER})
REVIEWER_ROLES = frozenset({UserRole.REVIEWER})


@dataclass(frozen=True)
class Rule:
    """One row of the section 9.2 table.

    ``roles`` empty means a system transition performed by ``performed_by``.
    ``then`` is the status the plan moves on to automatically, in the same request.
    """

    roles: frozenset[UserRole] = frozenset()
    performed_by: str | None = None
    reason_required: bool = False
    effect: Effect = Effect.NONE
    then: PlanStatus | None = None

    @property
    def requestable(self) -> bool:
        return bool(self.roles)


S = PlanStatus
TRANSITIONS: Mapping[tuple[PlanStatus, PlanStatus], Rule] = {
    (S.CREATED, S.CALIBRATION_PENDING): Rule(performed_by="plan creation"),
    (S.CALIBRATION_PENDING, S.EDITING): Rule(performed_by="calibration accept"),
    (S.EDITING, S.REVIEW_PENDING): Rule(TECHNICIAN_ROLES, effect=Effect.RELEASE_LEASE),
    (S.REVIEW_PENDING, S.IN_REVIEW): Rule(REVIEWER_ROLES, effect=Effect.ACQUIRE_LEASE),
    (S.IN_REVIEW, S.VALIDATED): Rule(REVIEWER_ROLES, effect=Effect.FREEZE),
    (S.IN_REVIEW, S.REJECTED): Rule(
        REVIEWER_ROLES, reason_required=True, effect=Effect.RELEASE_LEASE, then=S.EDITING
    ),
    (S.REJECTED, S.EDITING): Rule(performed_by="rejection"),
    (S.VALIDATED, S.EXPORTED): Rule(performed_by="final export"),
    (S.VALIDATED, S.REOPENED): Rule(REVIEWER_ROLES, effect=Effect.REOPEN, then=S.EDITING),
    (S.EXPORTED, S.REOPENED): Rule(REVIEWER_ROLES, effect=Effect.REOPEN, then=S.EDITING),
    (S.REOPENED, S.EDITING): Rule(performed_by="reopening"),
}


class TransitionConflictError(Exception):
    """The transition is not allowed now (409); ``code`` names the rule broken."""

    def __init__(self, message: str, code: str, extensions: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = dict(extensions or {})


class TransitionForbiddenError(Exception):
    """The user's role may not perform the transition (403).

    ``code`` is ``V-09`` for ``VALIDATED``; ``permission`` is what ``access.denied`` records.
    """

    def __init__(self, message: str, permission: str, code: str | None = None) -> None:
        super().__init__(message)
        self.permission = permission
        self.code = code


class TransitionInputError(Exception):
    """The request lacks a value the transition requires (422, V-09 for the reason)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def allowed_targets(status: PlanStatus) -> list[PlanStatus]:
    """Statuses a user may request from ``status``, in table order."""
    return [to for (frm, to), rule in TRANSITIONS.items() if frm is status and rule.requestable]


def _rule(current: PlanStatus, to: PlanStatus) -> Rule:
    rule = TRANSITIONS.get((current, to))
    allowed = {"allowed": [target.value for target in allowed_targets(current)]}
    if rule is None:
        message = f"A plan in status {current.value} cannot move to {to.value}."
        raise TransitionConflictError(message, ILLEGAL_CODE, allowed)
    if not rule.requestable:
        message = (
            f"{current.value} -> {to.value} is performed by the {rule.performed_by}, "
            "not on request."
        )
        raise TransitionConflictError(message, SYSTEM_CODE, allowed)
    return rule


def _check_role(rule: Rule, current: PlanStatus, to: PlanStatus, user: User) -> None:
    if user.role in rule.roles:
        return
    required = "reviewer" if rule.roles == REVIEWER_ROLES else "technician"
    if to is PlanStatus.VALIDATED:
        raise TransitionForbiddenError(V09_VALIDATE_MESSAGE, f"role:{required}", "V-09")
    message = f"The role {user.role.value} cannot move a plan from {current.value} to {to.value}."
    raise TransitionForbiddenError(message, f"role:{required}")


def _apply(db: Session, plan: Plan, user: User, effect: Effect, client_ip: str | None) -> None:
    """Run ``effect``; lease and revision conflicts become :class:`TransitionConflictError`."""
    try:
        if effect is Effect.RELEASE_LEASE:
            release_lease(db, plan, user, client_ip)
        elif effect is Effect.ACQUIRE_LEASE:
            acquire_lease(db, plan, user, client_ip)
        elif effect is Effect.FREEZE:
            revision = (
                db.get(Revision, plan.current_revision_id) if plan.current_revision_id else None
            )
            if revision is None:  # pragma: no cover - every plan has a current revision
                raise RevisionConflictError("The plan has no current revision.")
            freeze(db, revision, user, client_ip)
            plan.validated_revision_id = revision.id
            db.flush()
        elif effect is Effect.REOPEN:
            reopen(db, plan, user, client_ip)
    except LeaseConflictError as exc:
        raise TransitionConflictError(str(exc), exc.code, exc.extensions) from None
    except RevisionConflictError as exc:
        raise TransitionConflictError(str(exc), "revision_conflict") from None


def _step(
    db: Session,
    plan: Plan,
    user: User,
    to: PlanStatus,
    reason: str | None,
    client_ip: str | None,
) -> None:
    """Set ``plan.status`` to ``to`` and append its ``plan.transition`` event."""
    current = plan.status
    plan.status = to
    db.flush()
    after: dict[str, Any] = {"status": to.value}
    if to is PlanStatus.VALIDATED:
        after["validated_revision_id"] = str(plan.validated_revision_id)
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=EventKind.PLAN_TRANSITION,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=plan.current_revision_id,
        entity_id=plan.id,
        before={"status": current.value},
        after=after,
        reason=reason,
        client_ip=client_ip,
    )
    logger.info("User %s moved plan %s from %s to %s", user.id, plan.id, current, to)


def transition(
    db: Session,
    plan: Plan,
    user: User,
    to: PlanStatus,
    reason: str | None = None,
    client_ip: str | None = None,
) -> Plan:
    """Move ``plan`` to ``to`` on request of ``user`` following :data:`TRANSITIONS`.

    Locks the plan row. Raises :class:`TransitionConflictError` (illegal or system pair,
    lease held by another user, revision not freezable or reopenable),
    :class:`TransitionForbiddenError` (role) or :class:`TransitionInputError` (missing
    reason) before writing anything. A rule with ``then`` continues to that status in the
    same transaction. Runs in the caller's transaction; the caller commits.
    """
    db.refresh(plan, with_for_update=True)
    current = plan.status
    rule = _rule(current, to)
    _check_role(rule, current, to, user)
    reason = reason.strip() if reason is not None else None
    reason = reason or None
    if rule.reason_required and reason is None:
        raise TransitionInputError(V09_REASON_MESSAGE, "V-09")
    _apply(db, plan, user, rule.effect, client_ip)
    _step(db, plan, user, to, reason, client_ip)
    if rule.then is not None:
        follow = TRANSITIONS[(to, rule.then)]
        _apply(db, plan, user, follow.effect, client_ip)
        _step(db, plan, user, rule.then, None, client_ip)
    return plan
