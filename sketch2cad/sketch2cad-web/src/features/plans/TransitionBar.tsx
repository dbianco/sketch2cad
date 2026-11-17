// Plan transition buttons (spec section 9.2, US-14). The buttons follow the plan status and the
// user's role as the API's table does: technicians and reviewers send an EDITING plan to review;
// only reviewers take a review, validate, reject (with a reason, V-09) and reopen a validated or
// exported plan (V-10). The server re-checks everything; its errors are shown in Spanish.
import { useState } from "react";
import { api as defaultApi, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import type { PlanOut } from "../../editor/plan/planStore";
import { PLAN_MESSAGES as M, V10_MESSAGE, planErrorMessage, type PlanStatus } from "./messages";

export type UserRole = components["schemas"]["UserRole"];

export interface TransitionBarProps {
  plan: PlanOut;
  role: UserRole;
  /** Called with the plan the API returns after a transition. */
  onTransition(plan: PlanOut): void;
  api?: Pick<ApiClient, "post">;
}

export function TransitionBar({ plan, role, onTransition, api = defaultApi }: TransitionBarProps) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reviewer = role === "reviewer";
  const { status } = plan;

  async function transition(to: PlanStatus): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const next = await api.post("/api/v1/plans/{plan_id}/transition", {
        path: { plan_id: plan.id },
        body: to === "REJECTED" ? { to, reason: reason.trim() } : { to },
      });
      setReason("");
      onTransition(next);
    } catch (failure) {
      setError(planErrorMessage(failure, M.transitionFailed));
    } finally {
      setBusy(false);
    }
  }

  const button = (to: PlanStatus, label: string, disabled = false) => (
    <button type="button" disabled={busy || disabled} onClick={() => void transition(to)}>
      {label}
    </button>
  );
  const frozen = status === "VALIDATED" || status === "EXPORTED";

  return (
    <section aria-label={M.decision} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 18 }}>{M.decision}</h2>
      {status === "EDITING" && role !== "admin" && button("REVIEW_PENDING", M.submit)}
      {reviewer && status === "REVIEW_PENDING" && button("IN_REVIEW", M.takeReview)}
      {reviewer && status === "IN_REVIEW" && (
        <>
          <p style={{ margin: 0 }}>{M.decisionHint}</p>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {M.reasonLabel}
            <textarea value={reason} rows={3} onChange={(event) => setReason(event.target.value)} />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            {button("REJECTED", M.reject, reason.trim() === "")}
            {button("VALIDATED", M.validate)}
          </div>
        </>
      )}
      {frozen && <p style={{ margin: 0 }}>{V10_MESSAGE}</p>}
      {reviewer && frozen && button("REOPENED", M.reopen)}
      {error !== null && <p role="alert">{error}</p>}
    </section>
  );
}
