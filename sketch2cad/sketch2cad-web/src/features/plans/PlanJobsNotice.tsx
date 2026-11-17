// What the editor says about the jobs of the active calibration (features/jobs/usePlanJobs.ts):
// while the rectified image or the suggestions are being computed it says so, and when the
// latest `rectify` or `suggest_lines` job of the active calibration failed it shows the job's
// cause and action (JobFailure) with a link back to the calibration step, so a failed
// rectification never looks like "no suggestions".
//
// `useEntitiesReload` loads the revision's entities again when a `suggest_lines` job finishes.
// Like the batch panel it goes through `reloadEntities` (editor/store/serverOps.ts), which clears
// the undo history; it waits while the local queue has unsynced edits (the editor's sync sends
// them) so that the reload never replaces edits the server has not seen.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { PlanStore } from "../../editor/plan/planStore";
import type { EditorStore } from "../../editor/store/entities";
import { reloadEntities, type ServerOperationApi } from "../../editor/store/serverOps";
import { planCalibrationPath } from "../calibration/CalibrationPage";
import { JobFailure, jobFailureOf } from "../jobs/JobFailure";
import {
  RECTIFY_JOB_KIND,
  SUGGEST_JOB_KIND,
  isActiveJob,
  type PlanJob,
  type PlanJobs,
} from "../jobs/usePlanJobs";

export const PLAN_JOBS_TEXT = {
  rectifying: "Generando la imagen rectificada de la calibración…",
  suggesting: "Buscando sugerencias de líneas en el croquis…",
  stalled:
    "La imagen rectificada o las sugerencias siguen en preparación. Recarga la página en unos minutos para verlas.",
  rectifyFailed: "No se pudo generar la imagen rectificada; no habrá sugerencias automáticas",
  suggestFailed: "No se pudieron generar las sugerencias automáticas",
  recalibrate: "Volver a calibrar",
} as const;

/** Reload the entities on request, once the local queue is empty. */
export function useEntitiesReload(
  api: ServerOperationApi,
  store: StoreApi<EditorStore>,
  plan: StoreApi<PlanStore>,
): { request: () => void; error: string | null } {
  const revisionId = useStore(plan, (s) => s.revisionId);
  const [requested, setRequested] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const request = useCallback(() => setRequested((n) => n + 1), []);

  useEffect(() => {
    if (requested === 0 || revisionId === null) return;
    let active = true;
    let started = false;
    const run = () => {
      if (started || store.getState().queue.ops.length > 0) return;
      started = true;
      unsubscribe();
      void reloadEntities(api, store, { revisionId }).then((result) => {
        if (active) setError(result.ok ? null : result.message);
      });
    };
    const unsubscribe = store.subscribe(run);
    run();
    return () => {
      active = false;
      unsubscribe();
    };
  }, [requested, revisionId, api, store]);

  return { request, error };
}

/** Latest job of `kind` (jobs come newest first). */
function latest(jobs: readonly PlanJob[], kind: string): PlanJob | undefined {
  return jobs.find((job) => job.kind === kind);
}

export interface PlanJobsNoticeProps {
  jobs: PlanJobs;
  caseId: string;
  planId: string;
  /** Id of the active calibration; failures of earlier calibrations are not shown. */
  calibrationId: string | null;
  reloadError?: string | null;
}

export function PlanJobsNotice({ jobs, caseId, planId, calibrationId, reloadError }: PlanJobsNoticeProps) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const current = jobs.jobs.filter(
    (job) => calibrationId !== null && job.calibration_id === calibrationId,
  );
  const rectify = latest(current, RECTIFY_JOB_KIND);
  const suggest = latest(current, SUGGEST_JOB_KIND);
  const failures = [rectify, suggest].filter(
    (job): job is PlanJob => job?.status === "failed" && !dismissed.has(job.id),
  );
  const status =
    rectify !== undefined && isActiveJob(rectify)
      ? PLAN_JOBS_TEXT.rectifying
      : suggest !== undefined && isActiveJob(suggest)
        ? PLAN_JOBS_TEXT.suggesting
        : null;
  if (failures.length === 0 && status === null && !jobs.stalled && !reloadError) return null;

  return (
    <div style={{ padding: "0 12px", display: "flex", flexDirection: "column", gap: 6 }}>
      <p role="status" aria-live="polite" style={{ margin: 0 }}>
        {jobs.stalled ? PLAN_JOBS_TEXT.stalled : status}
      </p>
      {reloadError ? (
        <p role="alert" style={{ margin: 0, color: "#8C1D18" }}>
          {reloadError}
        </p>
      ) : null}
      {failures.map((job) => (
        <JobFailure
          key={job.id}
          error={jobFailureOf(job)}
          title={job.kind === RECTIFY_JOB_KIND ? PLAN_JOBS_TEXT.rectifyFailed : PLAN_JOBS_TEXT.suggestFailed}
          onDismiss={() => setDismissed((ids) => new Set([...ids, job.id]))}
        >
          <p style={{ margin: "6px 0 0" }}>
            <Link to={planCalibrationPath(caseId, planId)}>{PLAN_JOBS_TEXT.recalibrate}</Link>
          </p>
        </JobFailure>
      ))}
    </div>
  );
}
