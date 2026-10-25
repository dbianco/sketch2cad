// Plan state of the editor (jotdown H2): which plan and draft revision are open, their paper
// scale and local frame (from the active calibration) and the edit lease. Zustand vanilla, free of
// React; the editor tools and panels update it from API responses (`PlanOut`, `LeaseOut`).
import { createStore, type StoreApi } from "zustand/vanilla";
import type { components } from "../../api/schema";
import { effectivePaperScale, type LocalFrame } from "../frame/frame";

export type PlanOut = components["schemas"]["PlanOut"];
export type LeaseOut = components["schemas"]["LeaseOut"];

export interface PlanState {
  planId: string | null;
  /** Current (draft) revision, whose entities the editor shows. */
  revisionId: string | null;
  /** Whether the plan has an active (accepted) calibration. */
  calibrated: boolean;
  /** Declared paper scale denominator; `null` means the provisional 1:100. */
  paperScale: number | null;
  /** Stored local frame; `null` until one is set (entities are then in DEFAULT_FRAME). */
  localFrame: LocalFrame | null;
  /** Valid lease of the plan as last seen, whoever holds it. */
  lease: LeaseOut | null;
  /** Signed-in user, to tell whether `lease` is theirs. */
  userId: string | null;
  /** The editor only views the plan: its status forbids edits, or the lease is someone else's. */
  readOnly: boolean;
}

export interface PlanActions {
  /** Take plan, revision, calibration and lease from a `PlanOut`. */
  setPlan(plan: PlanOut): void;
  /** Record the lease returned by `POST /plans/{id}/lease`: the caller holds it. */
  acquiredLease(lease: LeaseOut): void;
  /** Record that nobody (or someone else, `lease`) holds the lease. */
  setLease(lease: LeaseOut | null): void;
  setUserId(userId: string | null): void;
  setReadOnly(readOnly: boolean): void;
  reset(): void;
}

export type PlanStore = PlanState & PlanActions;

export function initialPlanState(): PlanState {
  return {
    planId: null,
    revisionId: null,
    calibrated: false,
    paperScale: null,
    localFrame: null,
    lease: null,
    userId: null,
    readOnly: false,
  };
}

export function createPlanStore(initial: Partial<PlanState> = {}): StoreApi<PlanStore> {
  return createStore<PlanStore>()((set) => ({
    ...initialPlanState(),
    ...initial,
    setPlan: (plan) =>
      set({
        planId: plan.id,
        revisionId: plan.current_revision_id,
        calibrated: plan.calibration !== null,
        paperScale: plan.calibration?.paper_scale ?? null,
        localFrame: plan.calibration?.local_frame ?? null,
        lease: plan.lease,
      }),
    acquiredLease: (lease) => set({ lease, userId: lease.user_id }),
    setLease: (lease) => set({ lease }),
    setUserId: (userId) => set({ userId }),
    setReadOnly: (readOnly) => set({ readOnly }),
    reset: () => set(initialPlanState()),
  }));
}

/** The editor's plan store instance. */
export const planStore = createPlanStore();

/** Paper scale the API uses for frame conversions: declared, or the provisional 1:100. */
export function planPaperScale(state: Pick<PlanState, "paperScale">): number {
  return effectivePaperScale(state.paperScale);
}

/** Whether the signed-in user holds a lease that has not expired at `now` (ms since epoch). */
export function holdsLease(
  state: Pick<PlanState, "lease" | "userId">,
  now: number = Date.now(),
): boolean {
  const { lease, userId } = state;
  return (
    lease !== null &&
    userId !== null &&
    lease.user_id === userId &&
    Date.parse(lease.expires_at) > now
  );
}

/** The lease of another user that has not expired at `now`, if any (V-12). */
export function otherHolder(
  state: Pick<PlanState, "lease" | "userId">,
  now: number = Date.now(),
): LeaseOut | null {
  const { lease, userId } = state;
  return lease !== null && lease.user_id !== userId && Date.parse(lease.expires_at) > now
    ? lease
    : null;
}
