// Edit lease banner (V-12, spec section 9.2): holds the plan's lease while the editor is open and
// tells the user why the editor is read-only. With `manage` it POSTs the lease on mount, renews it
// every `renewMs` (the API's TTL is 10 minutes) while the user holds it, and DELETEs it on unmount
// (leaving the editor route). The calls run one after another, so a remount (StrictMode) releases
// before it acquires again. Closing the tab does not release: the lease then expires on its own.
//
// Any refused acquire or renewal (409 V-12 with the holder, 403) puts the plan store in read-only
// mode, which disables the palette and the canvas tools (editor/EditorWorkspace.tsx). The editor
// page does the same on a 409 from the entity sync.
import { useEffect, useRef } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { ApiError, api as defaultApi, type ApiClient } from "../../api/client";
import { holdsLease, otherHolder, planStore, type PlanStore } from "../../editor/plan/planStore";
import {
  EDITABLE_STATUSES,
  PLAN_MESSAGES as M,
  V10_MESSAGE,
  problemHolder,
  v12Message,
  type PlanStatus,
} from "./messages";

/** Renewal period of the own lease: half of the API's 10-minute TTL. */
export const LEASE_RENEW_MS = 5 * 60 * 1000;

type LeaseApi = Pick<ApiClient, "post" | "delete">;

/** Record a refused lease or edit call: who holds the lease, and read-only mode. */
export function enterReadOnly(plan: StoreApi<PlanStore>, error: unknown): void {
  const holder = problemHolder(error);
  if (holder !== null) plan.getState().setLease(holder);
  plan.getState().setReadOnly(true);
}

function useEditLease(
  plan: StoreApi<PlanStore>,
  api: LeaseApi,
  planId: string,
  manage: boolean,
  renewMs: number,
): void {
  const chain = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    if (!manage) return;
    const enqueue = (step: () => Promise<void>) => {
      chain.current = chain.current.then(step);
    };
    const acquire = (renewal: boolean) => async () => {
      try {
        const lease = await api.post("/api/v1/plans/{plan_id}/lease", { path: { plan_id: planId } });
        plan.getState().acquiredLease(lease);
      } catch (error) {
        // A failed renewal for a network error keeps the lease until it expires.
        if (!renewal || (error instanceof ApiError && [403, 409].includes(error.status))) {
          enterReadOnly(plan, error);
        }
      }
    };
    const release = async () => {
      if (!holdsLease(plan.getState())) return;
      try {
        await api.delete("/api/v1/plans/{plan_id}/lease", { path: { plan_id: planId } });
        plan.getState().setLease(null);
      } catch {
        // The lease expires on its own.
      }
    };

    enqueue(acquire(false));
    const timer = setInterval(() => {
      if (holdsLease(plan.getState())) enqueue(acquire(true));
    }, renewMs);
    return () => {
      clearInterval(timer);
      enqueue(release);
    };
  }, [plan, api, planId, manage, renewMs]);
}

const bannerStyle = { margin: 0, padding: "8px 12px", background: "#fff4ce" } as const;

export interface LeaseBannerProps {
  planId: string;
  status: PlanStatus;
  /** Acquire, renew and release the lease (the status is editable). */
  manage: boolean;
  plan?: StoreApi<PlanStore>;
  api?: LeaseApi;
  renewMs?: number;
}

export function LeaseBanner({
  planId,
  status,
  manage,
  plan = planStore,
  api = defaultApi,
  renewMs = LEASE_RENEW_MS,
}: LeaseBannerProps) {
  useEditLease(plan, api, planId, manage, renewMs);
  const holder = useStore(plan, (state) => otherHolder(state));
  const readOnly = useStore(plan, (state) => state.readOnly);

  let message: string | null = null;
  if (status === "VALIDATED" || status === "EXPORTED") message = V10_MESSAGE;
  else if (holder !== null) message = v12Message(holder.display_name);
  else if (readOnly) message = EDITABLE_STATUSES.has(status) ? M.leaseLost : M.notEditable;
  if (message === null) return null;
  return (
    <p role="status" className="lease-banner" style={bannerStyle}>
      {message}
    </p>
  );
}
