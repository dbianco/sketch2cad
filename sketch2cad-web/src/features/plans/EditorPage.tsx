// Editor page of a plan (route /cases/:caseId/plans/:planId/editor). It loads the plan, the active
// catalog, the current revision's entities and the source, fills the plan and editor stores and
// mounts EditorWorkspace. LeaseBanner holds the edit lease while the status is editable. Every
// operation queued by the editor store is sent `syncDelayMs` after the last one (flushQueue) while
// the user holds the lease; a 409 (V-12, lease_required, V-10) turns the editor read-only.
//
// The source image is placed as an axis-aligned rectangle: the image corners go through the
// calibration's image_to_paper homography to paper mm, then to the local frame, and the canvas
// stretches the preview over their bounding box. That is exact for scans; for photographs
// (perspective) and rotated frames the image is only approximately under the entities.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { api as defaultApi, type ApiClient } from "../../api/client";
import type { Catalog } from "../../editor/canvas/EditorCanvas";
import { boundsOf, type WorldRect } from "../../editor/canvas/viewport";
import { EditorWorkspace } from "../../editor/EditorWorkspace";
import { DEFAULT_FRAME, paperToWorld, worldToLocal } from "../../editor/frame/frame";
import {
  holdsLease,
  planPaperScale,
  planStore,
  type PlanOut,
  type PlanStore,
} from "../../editor/plan/planStore";
import { editorStore, type EditorStore } from "../../editor/store/entities";
import { flushQueue } from "../../editor/store/sync";
import type { Point2 } from "../../editor/store/types";
import { useAuth } from "../auth/authStore";
import { previewUrl } from "../sources/SourceViewer";
import { enterReadOnly, LeaseBanner } from "./LeaseBanner";
import { EDITABLE_STATUSES, PLAN_MESSAGES as M } from "./messages";
import { planReviewPath } from "./ReviewPage";

/** Quiet time after the last edit before the queue is sent. */
export const SYNC_DELAY_MS = 800;
const IMAGE_MAX_SIDE = 2048;

export type SyncStatus = "synced" | "pending" | "error";
const SYNC_LABELS: Readonly<Record<SyncStatus, string>> = {
  synced: M.syncDone,
  pending: M.syncPending,
  error: M.syncError,
};

/** Canvas rectangle (local frame, metres) covered by the source image of `width` x `height` px. */
export function imageBoundsOf(plan: PlanOut, width: number, height: number): WorldRect | null {
  const h = plan.calibration?.image_to_paper;
  if (h == null || h.length !== 3) return null;
  const scale = planPaperScale({ paperScale: plan.calibration?.paper_scale ?? null });
  const frame = plan.calibration?.local_frame ?? DEFAULT_FRAME;
  const corners: Point2[] = [[0, 0], [width, 0], [width, height], [0, height]];
  return boundsOf(
    corners.map(([x, y]) => {
      const [a = [], b = [], c = []] = h;
      const row = (r: number[]) => (r[0] ?? 0) * x + (r[1] ?? 0) * y + (r[2] ?? 0);
      const w = row(c);
      return worldToLocal(frame, scale, paperToWorld([row(a) / w, row(b) / w], scale));
    }),
  );
}

/** Send the editor store's queue after each burst of edits while the lease is held. */
export function useQueueSync(
  api: Pick<ApiClient, "post" | "patch" | "delete">,
  store: StoreApi<EditorStore>,
  plan: StoreApi<PlanStore>,
  delayMs: number,
): SyncStatus {
  const [status, setStatus] = useState<SyncStatus>("synced");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(() => void flush(), delayMs);
    };
    async function flush(): Promise<void> {
      const state = plan.getState();
      if (state.readOnly || state.revisionId === null || !holdsLease(state)) return;
      const result = await flushQueue(api, store, { revisionId: state.revisionId });
      if (disposed) return;
      if (result.status === "conflict") enterReadOnly(plan, result.error);
      if (result.status === "busy") schedule();
      else if (result.status !== "drained") setStatus("error");
      else setStatus(store.getState().queue.ops.length === 0 ? "synced" : "pending");
    }
    const unsubscribeStore = store.subscribe((next, prev) => {
      if (next.queue.nextSeq === prev.queue.nextSeq || next.queue.ops.length === 0) return;
      setStatus("pending");
      schedule();
    });
    const unsubscribePlan = plan.subscribe((next, prev) => {
      if (holdsLease(next) && !holdsLease(prev) && store.getState().queue.ops.length > 0) schedule();
    });
    return () => {
      disposed = true;
      clearTimeout(timer);
      unsubscribeStore();
      unsubscribePlan();
    };
  }, [api, store, plan, delayMs]);

  return status;
}

type Loaded =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; plan: PlanOut; catalog: Catalog; imageBounds: WorldRect | null };

export interface EditorPageProps {
  api?: ApiClient;
  store?: StoreApi<EditorStore>;
  plan?: StoreApi<PlanStore>;
  syncDelayMs?: number;
  renewMs?: number;
  /** Fixed canvas size (tests); the canvas fills its container otherwise. */
  width?: number;
  height?: number;
}

export function EditorPage({
  api = defaultApi,
  store = editorStore,
  plan = planStore,
  syncDelayMs = SYNC_DELAY_MS,
  renewMs,
  width,
  height,
}: EditorPageProps) {
  const { caseId = "", planId = "" } = useParams();
  const userId = useAuth().user?.id ?? null;
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const sync = useQueueSync(api, store, plan, syncDelayMs);
  const readOnly = useStore(plan, (s) => s.readOnly);

  useEffect(() => {
    let active = true;
    async function load(): Promise<void> {
      try {
        const loaded = await api.get("/api/v1/plans/{plan_id}", { path: { plan_id: planId } });
        const revisionId = loaded.current_revision_id;
        const [catalog, entities, source] = await Promise.all([
          api.get("/api/v1/catalog/active"),
          revisionId === null
            ? Promise.resolve({ entities: [] })
            : api.get("/api/v1/revisions/{revision_id}/entities", { path: { revision_id: revisionId } }),
          api.get("/api/v1/sources/{source_id}", { path: { source_id: loaded.primary_source_id } }),
        ]);
        if (!active) return;
        const editable = EDITABLE_STATUSES.has(loaded.status) && revisionId !== null;
        plan.getState().setPlan(loaded);
        plan.getState().setUserId(userId);
        plan.getState().setReadOnly(!editable);
        store.getState().load(revisionId ?? "", entities.entities);
        const imageBounds =
          source.width_px === null || source.height_px === null
            ? null
            : imageBoundsOf(loaded, source.width_px, source.height_px);
        setState({ kind: "ready", plan: loaded, catalog, imageBounds });
      } catch {
        if (active) setState({ kind: "error" });
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [api, store, plan, planId, userId]);

  if (state.kind === "loading") return <p role="status">{M.loading}</p>;
  if (state.kind === "error") return <p role="alert">{M.loadFailed}</p>;
  const loaded = state.plan;
  const manage = EDITABLE_STATUSES.has(loaded.status) && loaded.current_revision_id !== null;

  return (
    <section aria-label={M.editorTitle} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <header style={{ display: "flex", gap: 16, alignItems: "baseline", padding: "8px 12px" }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>{loaded.title}</h1>
        <Link to={planReviewPath(caseId, loaded.id)}>{M.openReview}</Link>
        <p role="status" aria-live="polite" style={{ margin: 0 }}>
          {readOnly && sync === "synced" ? null : SYNC_LABELS[sync]}
        </p>
      </header>
      <LeaseBanner
        planId={loaded.id}
        status={loaded.status}
        manage={manage}
        plan={plan}
        api={api}
        renewMs={renewMs}
      />
      <EditorWorkspace
        catalog={state.catalog}
        store={store}
        plan={plan}
        api={api}
        paperScale={planPaperScale({ paperScale: loaded.calibration?.paper_scale ?? null })}
        imageUrl={
          state.imageBounds === null
            ? undefined
            : previewUrl(loaded.primary_source_id, IMAGE_MAX_SIDE)
        }
        imageBounds={state.imageBounds ?? undefined}
        width={width}
        height={height}
      />
    </section>
  );
}
